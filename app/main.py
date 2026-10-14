"""OpenAI 호환 보안 게이트웨이.

기존 서비스는 base_url만 이 서버로 바꾸면 된다.
    client = OpenAI(base_url="http://localhost:8787/v1", api_key="...")
"""
from __future__ import annotations

import hashlib
import json
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import replace
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse

from . import guard, injection, pii, upstream
from .audit import AuditLog
from .config import Settings
from .scenarios import load_demo_cases, load_scenarios

STATIC = Path(__file__).parent / "static"


def _error(status: int, message: str, code: str, **extra: Any) -> JSONResponse:
    return JSONResponse(status_code=status, content={
        "error": {"message": message, "type": "policy_violation" if status == 400 else "gateway_error",
                  "code": code}, **extra})


def _sse(completion: dict[str, Any]):
    """응답 전체를 검사한 뒤 한 덩어리로 흘려보낸다. 토큰 단위 검사는 로드맵 항목."""
    base = {k: completion.get(k) for k in ("id", "created", "model")}
    base["object"] = "chat.completion.chunk"
    for choice in completion.get("choices", []):
        delta = {"role": "assistant", "content": choice.get("message", {}).get("content", "")}
        yield "data: " + json.dumps(
            {**base, "choices": [{"index": choice.get("index", 0), "delta": delta, "finish_reason": None}]},
            ensure_ascii=False) + "\n\n"
        yield "data: " + json.dumps(
            {**base, "choices": [{"index": choice.get("index", 0), "delta": {},
                                  "finish_reason": choice.get("finish_reason", "stop")}]}) + "\n\n"
    yield "data: [DONE]\n\n"


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    scenarios = load_scenarios() if settings.demo else {}
    if scenarios:
        secrets = (v for sc in scenarios.values() for v in sc.secrets.values())
        settings = replace(settings, secret_terms=tuple(dict.fromkeys([*settings.secret_terms, *secrets])))

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.audit = AuditLog(settings.audit_db)
        app.state.http = httpx.AsyncClient(timeout=settings.upstream_timeout_s)
        app.state.classifier = injection.load_classifier(settings.guard_model)
        yield
        await app.state.http.aclose()
        app.state.audit.close()

    app = FastAPI(title="Zero-Trust LLM Gateway", version="0.1.0", lifespan=lifespan)

    def client_id(request: Request) -> str:
        auth = request.headers.get("authorization", "")
        key = auth.removeprefix("Bearer ").strip()
        if settings.gateway_api_keys and key not in settings.gateway_api_keys:
            raise HTTPException(401, "게이트웨이 접속 키가 올바르지 않습니다.")
        return hashlib.sha256(key.encode()).hexdigest()[:8] if key else "anonymous"

    def require_admin(request: Request) -> None:
        if settings.admin_token and request.headers.get("x-admin-token") != settings.admin_token:
            raise HTTPException(401, "관리자 토큰이 필요합니다.")

    async def read_messages(request: Request) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        try:
            body = await request.json()
        except ValueError:
            raise HTTPException(400, "JSON 본문이 아닙니다.")
        messages = body.get("messages") if isinstance(body, dict) else None
        if not isinstance(messages, list) or not all(isinstance(m, dict) for m in messages):
            raise HTTPException(400, "messages 배열이 필요합니다.")
        return body, messages

    @app.post("/v1/chat/completions")
    async def chat_completions(request: Request):
        client = client_id(request)
        body, messages = await read_messages(request)
        request_id = uuid.uuid4().hex[:16]
        model = str(body.get("model", ""))

        inspection = guard.inspect(messages, settings, request.app.state.classifier)
        masked_prompt = "\n".join(
            f"[{m.get('role')}] {guard.text_of(m.get('content'))}" for m in inspection.masked_messages)
        headers = {"X-Guard-Request-Id": request_id, "X-Guard-Decision": inspection.decision}

        def log(masked_response: str, upstream_ms: float, detail: dict[str, Any]) -> None:
            request.app.state.audit.write(
                request_id=request_id, client=client, model=model, decision=inspection.decision,
                score=inspection.score, detail=detail, masked_prompt=masked_prompt,
                masked_response=masked_response, guard_ms=inspection.guard_ms, upstream_ms=upstream_ms)

        if inspection.decision == "block":
            summary = {**inspection.summary(), "request_id": request_id,
                       "latency_ms": {"guard": inspection.guard_ms, "upstream": 0.0}}
            log("", 0.0, inspection.summary())
            resp = _error(400, "보안 정책에 따라 차단된 요청입니다. (프롬프트 공격 의심)",
                          "prompt_injection_detected", guard=summary)
            resp.headers.update(headers)
            return resp

        started = time.perf_counter()
        try:
            completion = await upstream.complete(
                request.app.state.http, settings, {**body, "messages": inspection.masked_messages},
                request.headers.get("authorization"))
        except upstream.UpstreamError as e:
            log("", round((time.perf_counter() - started) * 1000, 2),
                {**inspection.summary(), "upstream_status": e.status})
            return JSONResponse(status_code=e.status, content=e.body, headers=headers)
        except httpx.HTTPError as e:
            log("", round((time.perf_counter() - started) * 1000, 2),
                {**inspection.summary(), "upstream_error": type(e).__name__})
            resp = _error(502, "LLM 서버에 연결하지 못했습니다.", "upstream_unreachable")
            resp.headers.update(headers)
            return resp
        upstream_ms = round((time.perf_counter() - started) * 1000, 2)

        # 응답에 기밀이 실려 나가면 통째로 막는다. 입력 검사를 통과한 요청의 두 번째 방어선.
        raw = "\n".join(str((c.get("message") or {}).get("content") or "") for c in completion.get("choices", []))
        leaks = guard.output_leak(raw, messages, settings)
        if leaks:
            inspection.decision = "block"
            detail = {**inspection.summary(), "stage": "output", "output_leak": leaks}
            log(pii.redact(raw, settings.secret_terms)[0], upstream_ms, detail)
            resp = _error(400, "보안 정책에 따라 응답을 차단했습니다. (기밀 유출 의심)", "sensitive_output_blocked",
                          guard={**detail, "request_id": request_id,
                                 "latency_ms": {"guard": inspection.guard_ms, "upstream": upstream_ms}})
            resp.headers.update({**headers, "X-Guard-Decision": "block"})
            return resp

        # 모델 응답에 새로 나온 개인정보는 가린다. 로그에는 가명 상태로 남기고,
        # 사용자가 보낸 값의 가명만 사용자에게 원래대로 되돌려 준다.
        logged, output_pii = [], {}
        for choice in completion.get("choices", []):
            message = choice.get("message") or {}
            if isinstance(message.get("content"), str):
                redacted, counts = pii.redact(message["content"], settings.secret_terms)
                logged.append(redacted)
                for kind, n in counts.items():
                    output_pii[kind] = output_pii.get(kind, 0) + n
                message["content"] = (inspection.masker.restore(redacted)
                                      if settings.restore_pii else redacted)

        summary = {**inspection.summary(), "output_pii": output_pii, "request_id": request_id,
                   "latency_ms": {"guard": inspection.guard_ms, "upstream": upstream_ms}}
        log("\n".join(logged), upstream_ms, {**inspection.summary(), "output_pii": output_pii})
        completion["guard"] = summary

        if body.get("stream"):
            return StreamingResponse(_sse(completion), media_type="text/event-stream", headers=headers)
        return JSONResponse(content=completion, headers=headers)

    @app.post("/guard/inspect")
    async def inspect_only(request: Request):
        """LLM을 부르지 않고 검사 결과만 본다. 평가 스크립트와 정책 시험용."""
        client_id(request)
        _, messages = await read_messages(request)
        inspection = guard.inspect(messages, settings, request.app.state.classifier)
        return {**inspection.summary(), "masked_messages": inspection.masked_messages,
                "latency_ms": {"guard": inspection.guard_ms}}

    @app.get("/v1/models")
    async def models(request: Request):
        client_id(request)
        if not settings.upstream_base_url:
            return {"object": "list", "data": [{"id": "mock", "object": "model", "owned_by": "gateway"}]}
        headers = ({"Authorization": f"Bearer {settings.upstream_api_key}"}
                   if settings.upstream_api_key else {})
        try:
            resp = await request.app.state.http.get(f"{settings.upstream_base_url}/models", headers=headers)
            return JSONResponse(status_code=resp.status_code, content=resp.json())
        except (httpx.HTTPError, ValueError):
            return _error(502, "LLM 서버에 연결하지 못했습니다.", "upstream_unreachable")

    @app.get("/admin/logs")
    def admin_logs(request: Request, limit: int = 50):
        require_admin(request)
        return request.app.state.audit.recent(max(1, min(limit, 500)))

    @app.get("/admin/stats")
    def admin_stats(request: Request):
        require_admin(request)
        return {**request.app.state.audit.stats(),
                "upstream": settings.upstream_base_url or "mock",
                "model_stage": bool(settings.guard_model)}

    @app.get("/admin/verify")
    def admin_verify(request: Request):
        require_admin(request)
        return request.app.state.audit.verify()

    @app.get("/demo/scenarios")
    def demo_scenarios(request: Request):
        """시연 콘솔이 고를 상황 목록. 시연 모드(DEMO=1)에서만 열린다."""
        require_admin(request)
        cases = load_demo_cases()
        return [{"id": sc.id, "name": sc.name, "system": sc.system, "context": sc.context,
                 "secrets": list(sc.secrets.values()), "cases": cases.get(sc.id, [])}
                for sc in scenarios.values()]

    @app.get("/healthz")
    def healthz():
        return {"ok": True}

    @app.get("/", include_in_schema=False)
    def console():
        return FileResponse(STATIC / "console.html")

    return app


app = create_app()
