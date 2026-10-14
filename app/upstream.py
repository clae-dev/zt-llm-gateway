"""실제 LLM 서버로 전달. 주소가 없으면 모의 응답으로 대신한다."""
from __future__ import annotations

import time
from typing import Any

import httpx

from .config import Settings
from .guard import text_of


class UpstreamError(Exception):
    def __init__(self, status: int, body: Any) -> None:
        super().__init__(f"upstream {status}")
        self.status, self.body = status, body


def _mock(body: dict[str, Any]) -> dict[str, Any]:
    # 모델이 실제로 받은 글을 그대로 돌려줘서, 마스킹이 됐는지 눈으로 확인할 수 있게 한다.
    last_user = next((m for m in reversed(body["messages"]) if m.get("role") == "user"), {})
    received = text_of(last_user.get("content"))
    return {
        "id": "chatcmpl-mock",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": body.get("model", "mock"),
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": f"(모의 응답) 모델이 받은 내용: {received}"},
            "finish_reason": "stop",
        }],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    }


async def complete(client: httpx.AsyncClient, settings: Settings, body: dict[str, Any],
                   client_auth: str | None) -> dict[str, Any]:
    if not settings.upstream_base_url:
        return _mock(body)

    headers = {"Content-Type": "application/json"}
    if settings.upstream_api_key:
        headers["Authorization"] = f"Bearer {settings.upstream_api_key}"
    elif client_auth and not settings.gateway_api_keys:
        # 게이트웨이 키를 따로 안 쓰는 구성에서만 클라이언트 키를 그대로 넘긴다.
        headers["Authorization"] = client_auth

    resp = await client.post(f"{settings.upstream_base_url}/chat/completions",
                             json={**body, "stream": False}, headers=headers)
    try:
        payload = resp.json()
    except ValueError:
        payload = {"error": {"message": resp.text[:500], "type": "upstream_error"}}
    if resp.status_code >= 400:
        raise UpstreamError(resp.status_code, payload)
    return payload
