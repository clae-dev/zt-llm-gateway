"""한글을 다른 글자로 감춘 입력을 풀어 읽는다.

- 한영타 전환: 한글 자판 그대로 영문 상태에서 친 글 (dlwjs wltl -> 이전 지시)
- 자모 분리: 낱자로 풀어 쓴 글 (ㅇㅣㅈㅓㄴ -> 이전)

탐지 모델과 규칙은 풀어 읽은 글도 함께 검사한다.
"""
from __future__ import annotations

CHO = "ㄱㄲㄴㄷㄸㄹㅁㅂㅃㅅㅆㅇㅈㅉㅊㅋㅌㅍㅎ"
JUNG = "ㅏㅐㅑㅒㅓㅔㅕㅖㅗㅘㅙㅚㅛㅜㅝㅞㅟㅠㅡㅢㅣ"
JONG = ["", "ㄱ", "ㄲ", "ㄳ", "ㄴ", "ㄵ", "ㄶ", "ㄷ", "ㄹ", "ㄺ", "ㄻ", "ㄼ", "ㄽ", "ㄾ", "ㄿ", "ㅀ",
        "ㅁ", "ㅂ", "ㅄ", "ㅅ", "ㅆ", "ㅇ", "ㅈ", "ㅊ", "ㅋ", "ㅌ", "ㅍ", "ㅎ"]
_VOWEL_PAIR = {("ㅗ", "ㅏ"): "ㅘ", ("ㅗ", "ㅐ"): "ㅙ", ("ㅗ", "ㅣ"): "ㅚ", ("ㅜ", "ㅓ"): "ㅝ",
               ("ㅜ", "ㅔ"): "ㅞ", ("ㅜ", "ㅣ"): "ㅟ", ("ㅡ", "ㅣ"): "ㅢ"}
_FINAL_PAIR = {("ㄱ", "ㅅ"): "ㄳ", ("ㄴ", "ㅈ"): "ㄵ", ("ㄴ", "ㅎ"): "ㄶ", ("ㄹ", "ㄱ"): "ㄺ", ("ㄹ", "ㅁ"): "ㄻ",
               ("ㄹ", "ㅂ"): "ㄼ", ("ㄹ", "ㅅ"): "ㄽ", ("ㄹ", "ㅌ"): "ㄾ", ("ㄹ", "ㅍ"): "ㄿ", ("ㄹ", "ㅎ"): "ㅀ",
               ("ㅂ", "ㅅ"): "ㅄ"}
_QWERTY = dict(zip("rRseEfaqQtTdwWczxvgkoiOjpuPhynbml",
                   "ㄱㄲㄴㄷㄸㄹㅁㅂㅃㅅㅆㅇㅈㅉㅊㅋㅌㅍㅎㅏㅐㅑㅒㅓㅔㅕㅖㅗㅛㅜㅠㅡㅣ"))
_JAMO = set(CHO) | set(JUNG) | set(JONG[1:])


def compose(text: str) -> str:
    """낱자가 이어진 부분을 음절로 묶는다. 낱자가 아닌 글자는 그대로 둔다."""
    out, i, n = [], 0, len(text)

    def at(k: int) -> str:
        return text[k] if k < n else "\0"  # 빈 문자열은 어떤 문자열에도 '포함'되므로 쓰지 않는다

    while i < n:
        c = text[i]
        if c in CHO and at(i + 1) in JUNG:
            vowel, j = text[i + 1], i + 2
            if (vowel, at(j)) in _VOWEL_PAIR:
                vowel, j = _VOWEL_PAIR[(vowel, text[j])], j + 1
            final = ""
            # 받침은 뒤에 모음이 오지 않을 때만 붙인다 (모음이 오면 다음 음절의 첫소리)
            if at(j) in JONG[1:] and at(j + 1) not in JUNG:
                final, j = text[j], j + 1
                if (final, at(j)) in _FINAL_PAIR and at(j + 1) not in JUNG:
                    final, j = _FINAL_PAIR[(final, text[j])], j + 1
            out.append(chr(0xAC00 + (CHO.index(c) * 21 + JUNG.index(vowel)) * 28 + JONG.index(final)))
            i = j
        else:
            out.append(c)
            i += 1
    return "".join(out)


def _quality(text: str) -> tuple[int, int]:
    syllables = sum("가" <= ch <= "힣" for ch in text)
    stray = sum(ch in _JAMO for ch in text)
    return syllables, stray


def from_qwerty(text: str) -> str | None:
    """영문 자판으로 친 한글을 되돌린다. 한글로 읽히지 않으면 None."""
    letters = [ch for ch in text if ch.isalpha()]
    if len(letters) < 6 or sum(ch.isascii() for ch in letters) / len(letters) < 0.8:
        return None
    mapped = "".join(_QWERTY.get(ch, _QWERTY.get(ch.lower(), ch)) for ch in text)
    converted = compose(mapped)
    syllables, stray = _quality(converted)
    return converted if syllables >= 3 and stray <= 0.2 * (syllables + stray) else None


def from_jamo(text: str) -> str | None:
    """낱자로 풀어 쓴 글을 묶는다. 묶이는 것이 거의 없으면 None."""
    if sum(ch in _JAMO for ch in text) < 4:
        return None
    converted = compose(text)
    return converted if converted != text and _quality(converted)[0] >= 2 else None
