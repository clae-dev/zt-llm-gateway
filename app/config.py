"""환경변수 기반 설정. 값이 없으면 외부 연결 없이 도는 모의(mock) 모드로 뜬다."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _csv(name: str) -> tuple[str, ...]:
    return tuple(v.strip() for v in os.environ.get(name, "").split(",") if v.strip())


@dataclass(frozen=True)
class Settings:
    # 비우면 모의 응답. 예) http://localhost:11434/v1 (Ollama), http://npu-server:8000/v1 (vLLM 등)
    upstream_base_url: str = ""
    upstream_api_key: str = ""
    upstream_timeout_s: float = 60.0

    # 게이트웨이 접속 키(쉼표 구분). 비우면 인증 없이 통과 - 데모 전용.
    gateway_api_keys: tuple[str, ...] = ()
    admin_token: str = ""

    block_threshold: float = 0.75
    flag_threshold: float = 0.40
    # 2차 탐지 모델이 공격이라고 본 확률이 이 값 이상이면 차단
    model_block_threshold: float = 0.50

    # 모델이 본 가명([전화번호#1])을 응답에서 원래 값으로 되돌릴지
    restore_pii: bool = True
    # 2차 탐지 모델. 이름에 kanana가 들어가면 카카오 Kanana Safeguard-Prompt로, 아니면 시퀀스 분류기로 읽는다.
    # 예) kakaocorp/kanana-safeguard-prompt-2.1b  (미리 내려받아 둔 것만 쓴다)
    guard_model: str = ""
    # 조직별 기밀 키워드(쉼표 구분). 걸리면 [기밀#n]으로 가린다.
    secret_terms: tuple[str, ...] = ()
    # 응답이 시스템 프롬프트를 이 글자 수 이상 그대로 옮기면 차단
    protect_system_prompt: bool = True
    system_overlap_chars: int = 30
    # 시연 모드. datasets/eval/scenarios 의 상황을 콘솔에서 고를 수 있고, 그 상황의 비밀 값을 기밀로 등록한다.
    demo: bool = False

    audit_db: Path = field(default_factory=lambda: ROOT / "data" / "audit.db")

    @classmethod
    def from_env(cls) -> "Settings":
        env = os.environ
        return cls(
            upstream_base_url=env.get("UPSTREAM_BASE_URL", "").rstrip("/"),
            upstream_api_key=env.get("UPSTREAM_API_KEY", ""),
            upstream_timeout_s=float(env.get("UPSTREAM_TIMEOUT_S", "60")),
            gateway_api_keys=_csv("GATEWAY_API_KEYS"),
            admin_token=env.get("ADMIN_TOKEN", ""),
            block_threshold=float(env.get("BLOCK_THRESHOLD", "0.75")),
            flag_threshold=float(env.get("FLAG_THRESHOLD", "0.40")),
            model_block_threshold=float(env.get("MODEL_BLOCK_THRESHOLD", "0.50")),
            protect_system_prompt=env.get("PROTECT_SYSTEM_PROMPT", "1") != "0",
            system_overlap_chars=int(env.get("SYSTEM_OVERLAP_CHARS", "30")),
            restore_pii=env.get("RESTORE_PII", "1") != "0",
            guard_model=env.get("GUARD_MODEL", ""),
            secret_terms=_csv("SECRET_TERMS"),
            demo=env.get("DEMO", "0") == "1",
            audit_db=Path(env.get("AUDIT_DB", str(ROOT / "data" / "audit.db"))),
        )
