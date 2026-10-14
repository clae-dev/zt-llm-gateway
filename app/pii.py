"""개인정보·비밀값 탐지와 마스킹 (1차: 정규식 + 검증식).

이름·주소처럼 형태가 정해지지 않은 항목은 정규식으로 잡을 수 없다.
그 부분은 NER 모델 단계(로드맵)에서 Detector를 추가해 붙인다.
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from typing import Callable, Iterable


@dataclass(frozen=True)
class Finding:
    kind: str
    start: int
    end: int
    value: str


@dataclass(frozen=True)
class Detector:
    kind: str
    pattern: re.Pattern[str]
    validate: Callable[[re.Match[str]], bool] | None = None
    group: int = 0


def _digits(s: str) -> list[int]:
    return [int(c) for c in s if c.isdigit()]


def _valid_rrn(m: re.Match[str]) -> bool:
    # 2020년 10월 이후 발급분은 검증번호 규칙이 없으므로 날짜 유효성만 본다.
    d = _digits(m.group())
    month, day = d[2] * 10 + d[3], d[4] * 10 + d[5]
    return 1 <= month <= 12 and 1 <= day <= 31


def _valid_card(m: re.Match[str]) -> bool:
    d = _digits(m.group())
    total = 0
    for i, n in enumerate(reversed(d)):
        if i % 2:
            n = n * 2 - 9 if n > 4 else n * 2
        total += n
    return total % 10 == 0


def _valid_biz(m: re.Match[str]) -> bool:
    d = _digits(m.group())
    total = sum(a * b for a, b in zip(d[:9], (1, 3, 7, 1, 3, 7, 1, 3, 5))) + d[8] * 5 // 10
    if (10 - total % 10) % 10 == d[9]:
        return True
    # 검증식이 틀려도 앞에 '사업자'라고 써 있으면 가린다(오타·예시 번호 대비).
    return "사업자" in m.string[max(0, m.start() - 20):m.start()]


# 앞에 있을수록 우선한다. 겹치면 먼저 잡힌 쪽이 이긴다.
DETECTORS: tuple[Detector, ...] = (
    Detector("비밀키", re.compile(
        r"sk-[A-Za-z0-9_\-]{20,}|AKIA[0-9A-Z]{16}|ghp_[A-Za-z0-9]{36}"
        r"|-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----")),
    Detector("이메일", re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)+")),
    Detector("주민등록번호", re.compile(r"(?<!\d)\d{6}[-\s]?[1-8]\d{6}(?!\d)"), _valid_rrn),
    Detector("카드번호", re.compile(r"(?<!\d)(?:\d{4}[-\s]?){3}\d{4}(?!\d)"), _valid_card),
    Detector("사업자등록번호", re.compile(r"(?<!\d)\d{3}-\d{2}-\d{5}(?!\d)"), _valid_biz),
    Detector("전화번호", re.compile(
        r"(?<!\d)(?:\+82[-\s]?1[016789]|01[016789])[-.\s]?\d{3,4}[-.\s]?\d{4}(?!\d)")),
    Detector("전화번호", re.compile(r"(?<!\d)0(?:2|[3-6][1-5])[-.\s]\d{3,4}[-.\s]?\d{4}(?!\d)")),
    Detector("계좌번호", re.compile(
        r"(?:계좌|입금|송금|은행|뱅크)[^\d\n]{0,15}(\d{2,6}(?:-\d{2,6}){1,3}|\d{10,14})(?!\d)"), group=1),
    Detector("여권번호", re.compile(r"(?<![A-Za-z0-9])[MSRODG]\d{8}(?!\d)")),
)


def find(text: str, secret_terms: Iterable[str] = ()) -> list[Finding]:
    candidates: list[tuple[int, Finding]] = []
    for term in secret_terms:
        for m in re.finditer(re.escape(term), text, re.IGNORECASE):
            candidates.append((-1, Finding("기밀", m.start(), m.end(), m.group())))
    for priority, det in enumerate(DETECTORS):
        for m in det.pattern.finditer(text):
            if det.validate and not det.validate(m):
                continue
            candidates.append(
                (priority, Finding(det.kind, m.start(det.group), m.end(det.group), m.group(det.group))))

    accepted: list[Finding] = []
    for _, f in sorted(candidates, key=lambda c: (c[0], c[1].start)):
        if all(f.end <= a.start or f.start >= a.end for a in accepted):
            accepted.append(f)
    return sorted(accepted, key=lambda f: f.start)


class Masker:
    """요청 하나 동안 '원래 값 <-> 가명' 대응표를 들고 있는다.

    같은 값은 같은 가명으로 바뀌므로 모델이 문맥을 잃지 않고,
    응답이 돌아오면 restore()로 사용자에게만 원래 값을 되돌려 준다.
    대응표는 메모리에만 있고 감사 로그에는 남기지 않는다.
    """

    def __init__(self, secret_terms: Iterable[str] = ()) -> None:
        self._secret_terms = tuple(secret_terms)
        self._by_value: dict[tuple[str, str], str] = {}
        self._originals: dict[str, str] = {}
        self.counts: Counter[str] = Counter()

    def mask(self, text: str) -> str:
        out, cursor = [], 0
        for f in find(text, self._secret_terms):
            key = (f.kind, f.value)
            if key not in self._by_value:
                self.counts[f.kind] += 1
                placeholder = f"[{f.kind}#{self.counts[f.kind]}]"
                self._by_value[key] = placeholder
                self._originals[placeholder] = f.value
            out += [text[cursor:f.start], self._by_value[key]]
            cursor = f.end
        out.append(text[cursor:])
        return "".join(out)

    def restore(self, text: str) -> str:
        # 모델이 가명을 "전화번호 #1"처럼 괄호를 빼거나 띄어 써서 돌려주는 경우까지 받아낸다.
        for placeholder, original in self._originals.items():
            kind, number = placeholder[1:-1].split("#")
            pattern = rf"(?:\[\s*)?{re.escape(kind)}\s*#\s*{number}(?!\d)(?:\s*\])?"
            text = re.sub(pattern, lambda _: original, text)
        return text


def redact(text: str, secret_terms: Iterable[str] = ()) -> tuple[str, Counter[str]]:
    """되돌릴 수 없는 가림. 모델 응답에 새로 나타난 개인정보에 쓴다."""
    out, cursor, counts = [], 0, Counter()
    for f in find(text, secret_terms):
        counts[f.kind] += 1
        out += [text[cursor:f.start], f"[{f.kind} 가림]"]
        cursor = f.end
    out.append(text[cursor:])
    return "".join(out), counts
