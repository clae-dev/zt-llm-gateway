"""프롬프트 인젝션·탈옥 탐지.

1차는 규칙(정규식), 2차는 분류 모델이다. 2차 모델은 GUARD_MODEL을 지정했을 때만 켜진다.
규칙은 설명 가능하고 빠르지만 돌려 말한 공격에 약하다. 그 구멍을 모델이 메운다.
"""
from __future__ import annotations

import base64
import binascii
import re
import unicodedata
from dataclasses import dataclass
from typing import Protocol

from . import hangul

_ZERO_WIDTH = re.compile(r"[​-‏⁠-⁤﻿­]")
_NOT_WORD = re.compile(r"[^0-9a-z가-힣]")
_BASE64 = re.compile(r"[A-Za-z0-9+/]{24,}={0,2}")


@dataclass(frozen=True)
class Rule:
    id: str
    category: str
    weight: float
    pattern: re.Pattern[str]
    # norm: 소문자·공백 정리본 / compact: 띄어쓰기·기호까지 지운 본(띄 어 쓰 기 우회 대응) / raw: 원문
    target: str = "norm"


@dataclass(frozen=True)
class Hit:
    id: str
    category: str
    weight: float
    excerpt: str


def _r(p: str, flags: int = 0) -> re.Pattern[str]:
    return re.compile(p, flags)


RULES: tuple[Rule, ...] = (
    Rule("R01", "지시 무력화", 0.85, _r(
        r"(이전|위|앞|기존|지금까지|여태까지|처음)(의|에서|에)?(받은|주어진|내린|말한)?(모든|전체)?"
        r"(지시|명령|지침|규칙|프롬프트|설정|제약|가이드라인)(사항|들)?(을|를|은|는|도|따위)?(전부|모두|다|싹)?"
        r"(무시|잊|따르지마|따르지말|버리|버려|취소|삭제|리셋|초기화)"), "compact"),
    Rule("R02", "지시 무력화", 0.85, _r(
        r"(ignore|disregard|forget|override)\s+(all\s+|any\s+)?(of\s+)?(the\s+|your\s+|my\s+)?"
        r"(previous|prior|above|earlier|preceding|initial|original)\s+"
        r"(instructions?|prompts?|rules?|directions?|guidelines?|messages?)")),
    Rule("R03", "시스템 프롬프트 유출", 0.80, _r(
        r"((시스템|system|초기|숨겨진)(프롬프트|prompt|지시문|지침|메시지)"
        r"|(너의|네|당신의|니)(프롬프트|지시문|지침|규칙))"
        r"(을|를|의)?(내용|전문|전체|원문)?(을|를)?(그대로)?"
        r"(출력|보여|알려|공개|말해|적어|복사|반복|번역|요약)"), "compact"),
    Rule("R04", "시스템 프롬프트 유출", 0.80, _r(
        r"(reveal|show|print|repeat|output|display|tell me|what (is|are|was|were))\s+(me\s+)?(all\s+)?(of\s+)?"
        r"(the\s+|your\s+)?(system|initial|hidden|original)\s+(prompt|instructions?|message)"
        r"|repeat\s+(the\s+|all\s+)?(words|text|everything)\s+above")),
    Rule("R05", "탈옥 페르소나", 0.80, _r(
        r"do anything now|\bdan\s?(mode|모드)|developer mode|jailbreak mode|개발자\s?모드|탈옥\s?모드|무검열"
        r"|uncensored|no restrictions|without (any )?(restrictions|filters|limitations)")),
    # 단어만 나온 경우는 보안 질문일 수 있어 차단하지 않고 표시만 한다.
    Rule("R16", "탈옥 페르소나", 0.40, _r(r"탈옥|jailbr(eak|oken)")),
    Rule("R06", "제한 해제 요구", 0.50, _r(
        r"(제한|제약|검열|필터|윤리|도덕|안전장치|가드레일|정책|규정)(이|가|은|는|을|를|도|따위)?(전혀|아예)?"
        r"(없는|없이|없다고|해제|풀어|풀린|꺼|끄고|무시|우회|상관없)"), "compact"),
    # 역할 지정은 정상 요청에도 흔해서 단독으로는 통과시키고, 다른 규칙과 겹칠 때만 점수를 올린다.
    Rule("R07", "역할 탈취", 0.35, _r(
        r"you are now|from now on,? you|act as (if|though)|pretend (to be|you are)|roleplay as")),
    Rule("R08", "역할 탈취", 0.35, _r(
        r"(너는|넌|당신은)(이제|지금)(부터)?|(이제|지금)(부터)?(너는|넌|당신은)"), "compact"),
    Rule("R09", "구분자 위조", 0.75, _r(
        r"<\|(im_start|im_end|system|endoftext)\|>|\[/?inst\]|<<sys>>|^\s*(###\s*)?(system|assistant)\s*:",
        re.IGNORECASE | re.MULTILINE), "raw"),
    Rule("R10", "인코딩 우회", 0.50, _r(
        r"(base64|rot13|hex|16진수|유니코드|모스\s?부호).{0,30}(decode|디코드|디코딩|해독|복호화|풀어)")),
    Rule("R11", "대량 정보 추출", 0.55, _r(
        r"(모든|전체|전부)(고객|직원|임직원|사용자|회원|환자)(의)?(개인)?(정보|데이터|목록|명단|연락처)(를|을)?"
        r"(전부|모두|다)?(출력|보여|내보내|추출|덤프|알려|나열)"), "compact"),
    Rule("R12", "새 지시 주입", 0.50, _r(
        r"(새로운|새)(지시|명령|지침|규칙)(사항)?(이다|입니다|이야|을따라|를따라|에따라)"), "compact"),
    Rule("R13", "새 지시 주입", 0.50, _r(r"(new|updated) instructions?\s*:")),
    Rule("R14", "응답 강제", 0.50, _r(
        r"(start|begin) (your )?(response|answer|reply) with|never refuse|do not refuse"
        r"|you (cannot|can't|must not) refuse")),
    Rule("R15", "응답 강제", 0.50, _r(
        r"(거절|거부)(하지마|하지말|하면안|은금지|할수없)|절대(로)?(거절|거부)"
        r"|(답변|응답|대답)(은|을)?(반드시|무조건)?.{0,8}(로|으로)시작"), "compact"),
)


def normalize(text: str) -> tuple[str, str]:
    norm = unicodedata.normalize("NFKC", _ZERO_WIDTH.sub("", text)).lower()
    norm = re.sub(r"\s+", " ", norm)
    return norm, _NOT_WORD.sub("", norm)


def _excerpt(source: str, m: re.Match[str]) -> str:
    return source[max(0, m.start() - 8):m.end() + 8].strip()[:80]


def _scan_rules(text: str) -> list[Hit]:
    norm, compact = normalize(text)
    views = {"norm": norm, "compact": compact, "raw": text}
    hits = []
    for rule in RULES:
        m = rule.pattern.search(views[rule.target])
        if m:
            hits.append(Hit(rule.id, rule.category, rule.weight, _excerpt(views[rule.target], m)))
    return hits


def _decoded_payloads(text: str) -> list[str]:
    out = []
    for chunk in _BASE64.findall(text):
        try:
            decoded = base64.b64decode(chunk + "=" * (-len(chunk) % 4), validate=True).decode("utf-8")
        except (binascii.Error, UnicodeDecodeError):
            continue
        if decoded.isprintable() or "\n" in decoded:
            out.append(decoded)
    return out


# 풀어 읽은 글에서 규칙이 걸리면 붙는 표시. 글자를 감춘 것 자체가 강한 신호라 단독으로도 차단한다.
_VARIANT_RULE = {"base64": "R90", "한영타 전환": "R91", "자모 분리": "R92"}
HARD_RULES = {"R09", *_VARIANT_RULE.values()}


def expand(text: str) -> list[tuple[str, str]]:
    """(종류, 글) 목록. 첫 항목은 원문이고, 나머지는 감춘 글자를 풀어 읽은 것이다."""
    variants = [("원문", text)]
    variants += [("base64", payload) for payload in _decoded_payloads(text)]
    for kind, converted in (("한영타 전환", hangul.from_qwerty(text)), ("자모 분리", hangul.from_jamo(text))):
        if converted:
            variants.append((kind, converted))
    return variants


def scan(text: str) -> list[Hit]:
    hits = _scan_rules(text)
    for kind, variant in expand(text)[1:]:
        inner = _scan_rules(variant)
        if inner:
            hits.append(Hit(_VARIANT_RULE[kind], "글자 감추기", 0.60, f"{kind}으로 감춘 지시: {variant[:50]}"))
            hits += inner
    return hits


def rule_score(hits: list[Hit]) -> float:
    safe = 1.0
    for h in hits:
        safe *= 1.0 - h.weight
    return round(1.0 - safe, 3)


class Classifier(Protocol):
    name: str

    def classify(self, text: str) -> tuple[float, str]:
        """(공격일 확률 0~1, 판정 설명)."""


class HFClassifier:
    """HuggingFace 시퀀스 분류 모델 래퍼. 라벨 1(또는 이름에 inject/jailbreak/attack)을 공격으로 본다."""

    def __init__(self, model_path: str) -> None:
        import torch
        from transformers import AutoModelForSequenceClassification, AutoTokenizer

        self.name = model_path.rstrip("/").split("/")[-1]
        self._torch = torch
        self._tok = AutoTokenizer.from_pretrained(model_path, local_files_only=True)
        self._model = AutoModelForSequenceClassification.from_pretrained(model_path, local_files_only=True)
        self._device = "cuda" if torch.cuda.is_available() else "cpu"
        self._model.to(self._device).eval()
        labels = {i: str(n).lower() for i, n in self._model.config.id2label.items()}
        named = [i for i, n in labels.items() if any(k in n for k in ("inject", "jailbreak", "attack"))]
        self._attack_index = named[0] if named else max(labels)

    def classify(self, text: str) -> tuple[float, str]:
        enc = self._tok(text, truncation=True, max_length=512, return_tensors="pt").to(self._device)
        with self._torch.no_grad():
            probs = self._model(**enc).logits.softmax(-1)[0]
        return float(probs[self._attack_index]), "프롬프트 공격"


class KananaClassifier:
    """카카오 Kanana Safeguard-Prompt 래퍼 (Apache-2.0).

    모델은 판정 토큰 하나(<SAFE>, <UNSAFE-A1>, <UNSAFE-A2>)만 내놓는다.
    그 세 토큰의 확률을 꺼내 점수와 유형 설명으로 바꿔, 왜 막았는지를 로그에 남길 수 있게 한다.
    """

    LABELS = {"<UNSAFE-A1>": "프롬프트 인젝션", "<UNSAFE-A2>": "프롬프트 유출"}

    def __init__(self, model_path: str) -> None:
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer, LlamaConfig

        # hidden 1792, head 24개, head_dim 128을 따로 지정한 구조라 transformers 5.x의 설정 검증에 걸린다.
        # 모델 코드는 head_dim을 지원하므로 그 검증 하나만 끈다.
        def skip(self):
            return None

        skip.__name__ = "validate_architecture"
        LlamaConfig.validate_architecture = skip
        validators = getattr(LlamaConfig, "__class_validators__", None)
        if validators is not None:
            LlamaConfig.__class_validators__ = [v for v in validators if v.__name__ != "validate_architecture"]

        self.name = "Kanana Safeguard-Prompt"
        self._torch = torch
        self._tok = AutoTokenizer.from_pretrained(model_path, local_files_only=True)
        device = "cuda" if torch.cuda.is_available() else "cpu"
        self._model = AutoModelForCausalLM.from_pretrained(
            model_path, local_files_only=True,
            dtype=torch.float16 if device == "cuda" else torch.float32, device_map=device).eval()
        vocab = self._tok.get_vocab()
        self._tokens = ["<SAFE>", *self.LABELS]
        self._ids = [vocab[t] for t in self._tokens]

    def classify(self, text: str) -> tuple[float, str]:
        enc = self._tok.apply_chat_template([{"role": "user", "content": text}], tokenize=True,
                                            return_tensors="pt", return_dict=True).to(self._model.device)
        with self._torch.no_grad():
            logits = self._model(**enc).logits[0, -1].float()
        probs = logits[self._ids].softmax(-1).tolist()
        unsafe = max(range(1, 3), key=lambda i: probs[i])
        return round(1.0 - probs[0], 4), self.LABELS[self._tokens[unsafe]]

    def close(self) -> None:
        del self._model
        self._torch.cuda.empty_cache()


def load_classifier(model_path: str) -> Classifier | None:
    if not model_path:
        return None
    return KananaClassifier(model_path) if "kanana" in model_path.lower() else HFClassifier(model_path)
