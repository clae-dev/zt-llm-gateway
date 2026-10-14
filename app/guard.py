"""요청 하나를 검사하는 파이프라인: 공격 탐지 -> 개인정보 마스킹 -> 판정, 그리고 응답 쪽 유출 검사."""
from __future__ import annotations

import copy
import re
import time
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Any

from . import injection, pii
from .config import Settings

# 사용자가 직접 쓴 글과, 도구·검색 결과로 들어온 글(간접 인젝션)을 검사한다.
SCANNED_ROLES = {"user", "tool", "function"}

# 가명이 들어간 요청에만 앞에 붙인다. 모델이 가명을 풀어 쓰면 응답에서 되돌릴 수 없기 때문.
PLACEHOLDER_NOTE = (
    "대화에 [전화번호#1], [이메일#1] 처럼 대괄호와 #번호로 된 표시가 나오면 보안상 가려 둔 값이다. "
    "답변에 써야 할 때는 대괄호와 번호까지 글자 하나 바꾸지 말고 그대로 적는다. "
    "이 표시가 가려진 값이라는 점은 답변에서 설명하거나 언급하지 않는다."
)


@dataclass
class Inspection:
    decision: str  # allow | flag | block
    score: float
    hits: list[injection.Hit]
    model_score: float | None
    masked_messages: list[dict[str, Any]]
    masker: pii.Masker
    guard_ms: float = 0.0
    extra: dict[str, Any] = field(default_factory=dict)

    def summary(self) -> dict[str, Any]:
        seen, hits = set(), []
        for h in self.hits:
            if h.id not in seen:
                seen.add(h.id)
                hits.append({"id": h.id, "category": h.category, "weight": h.weight, "excerpt": h.excerpt})
        return {
            "decision": self.decision,
            "score": self.score,
            "model_score": self.model_score,
            "hits": hits,
            "pii": dict(self.masker.counts),
            **self.extra,
        }


def _map_text(content: Any, fn) -> Any:
    """content가 문자열이든 멀티모달 파트 배열이든 텍스트 부분에만 fn을 적용한다."""
    if isinstance(content, str):
        return fn(content)
    if isinstance(content, list):
        return [
            {**part, "text": fn(part["text"])}
            if isinstance(part, dict) and isinstance(part.get("text"), str) else part
            for part in content
        ]
    return content


def text_of(content: Any) -> str:
    parts: list[str] = []
    _map_text(content, lambda t: parts.append(t) or t)
    return "\n".join(parts)


def decide(rule_hits: list[injection.Hit], model_score: float | None, settings: Settings) -> tuple[str, float]:
    """규칙과 모델을 합쳐 판정한다.

    모델이 있으면 뜻은 모델이 판단하고, 규칙은 글자를 감춘 흔적 같은 구조적 신호만 단독 차단 권한을 갖는다.
    낱말만 보고 걸리는 규칙은 '주의'로 기록만 한다. 그래야 "탈옥 공격을 막는 방법" 같은 정상 질문을 막지 않는다.
    """
    rules = injection.rule_score(rule_hits)
    if model_score is None:
        score = rules
        if score >= settings.block_threshold:
            return "block", score
        return ("flag" if score >= settings.flag_threshold else "allow"), score

    score = max(rules, model_score)
    if model_score >= settings.model_block_threshold or any(h.id in injection.HARD_RULES for h in rule_hits):
        return "block", score
    return ("flag" if score >= settings.flag_threshold else "allow"), score


def inspect(messages: list[dict[str, Any]], settings: Settings,
            classifier: injection.Classifier | None = None) -> Inspection:
    started = time.perf_counter()

    hits: list[injection.Hit] = []
    model_score: float | None = None
    model_hit: injection.Hit | None = None
    for msg in messages:
        if msg.get("role") not in SCANNED_ROLES:
            continue
        text = text_of(msg.get("content"))
        hits += injection.scan(text)
        if classifier and text.strip():
            # 감춘 글자를 풀어 읽은 글도 모델에 보인다
            for kind, variant in injection.expand(text):
                score, label = classifier.classify(variant)
                if model_score is None or score > model_score:
                    model_score = score
                    model_hit = injection.Hit("M01", f"모델 판정: {label}", score,
                                              f"{classifier.name} 확률 {score:.2f} ({kind})")

    decision, score = decide(hits, model_score, settings)
    if model_hit and model_hit.weight >= settings.flag_threshold:
        hits = [model_hit, *hits]

    # 운영자가 쓴 시스템 메시지는 건드리지 않는다. 사용자 쪽에서 들어온 값만 가명으로 바꾸고 되돌린다.
    masker = pii.Masker(settings.secret_terms)
    masked = copy.deepcopy(messages)
    for msg in masked:
        if msg.get("role") != "system":
            msg["content"] = _map_text(msg.get("content"), masker.mask)
    if masker.counts:
        masked.insert(0, {"role": "system", "content": PLACEHOLDER_NOTE})

    result = Inspection(decision, round(score, 3), hits, model_score, masked, masker)
    result.guard_ms = round((time.perf_counter() - started) * 1000, 2)
    return result


_SPACE = re.compile(r"\s+")


def output_leak(response: str, messages: list[dict[str, Any]], settings: Settings) -> list[str]:
    """응답에 기밀이 실려 나가는지 본다. 입력 검사를 통과한 요청의 두 번째 방어선이다.

    돌려 말한 요청("맨 위 문단을 번역해줘")은 문장만 보면 정상이라 입력 단계에서 못 막는다.
    그 결과로 무엇이 나가는지는 여기서만 보인다. 사유에는 기밀 값 자체를 적지 않는다.
    """
    reasons = []
    lowered = response.lower()
    for i, term in enumerate(settings.secret_terms, 1):
        if term.lower() in lowered:
            reasons.append(f"등록 기밀 {i}번이 응답에 포함됨")
    if settings.protect_system_prompt:
        system = _SPACE.sub("", "".join(text_of(m.get("content")) for m in messages if m.get("role") == "system"))
        body = _SPACE.sub("", response)
        if system and body:
            longest = SequenceMatcher(None, system, body, autojunk=False).find_longest_match().size
            if longest >= settings.system_overlap_chars:
                reasons.append(f"시스템 프롬프트와 {longest}자 연속 일치")
    return reasons
