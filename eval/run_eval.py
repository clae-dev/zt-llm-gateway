"""탐지 성능과 처리 시간을 잰다.  python -m eval.run_eval [--show-errors]

같이 든 표본은 동작 확인용이다. 규칙을 쓴 사람이 표본도 썼으므로 점수가 실제보다 높게 나온다.
사업계획서의 목표치(F1 0.9 · 재현율 95%) 검증은 따로 모은 평가셋으로 해야 한다.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

from app import guard, pii
from app.config import Settings

HERE = Path(__file__).parent


def load(name: str) -> list[dict]:
    return [json.loads(line) for line in (HERE / name).read_text(encoding="utf-8").splitlines() if line.strip()]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--show-errors", action="store_true")
    parser.add_argument("--attack-file", default="attack_samples.jsonl")
    parser.add_argument("--pii-file", default="pii_samples.jsonl")
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    settings = Settings()

    samples = load(args.attack_file)
    tp = fp = fn = tn = 0
    errors, times = [], []
    for s in samples:
        started = time.perf_counter()
        result = guard.inspect([{"role": "user", "content": s["text"]}], settings)
        times.append((time.perf_counter() - started) * 1000)
        detected = result.decision != "allow"
        if s["label"] and detected:
            tp += 1
        elif s["label"]:
            fn += 1
            errors.append(("놓침", result.score, s["text"]))
        elif detected:
            fp += 1
            errors.append(("오탐", result.score, s["text"]))
        else:
            tn += 1
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0

    print(f"[프롬프트 공격 탐지] 표본 {len(samples)}건 (공격 {tp + fn} / 정상 {fp + tn})")
    print(f"  정밀도 {precision:.3f}   재현율 {recall:.3f}   F1 {f1:.3f}")
    print(f"  맞힘 {tp}   놓침 {fn}   오탐 {fp}   정상 통과 {tn}")

    pii_samples = load(args.pii_file)
    found = total = 0
    for s in pii_samples:
        values = {f.value for f in pii.find(s["text"])}
        for expected in s["expect"]:
            total += 1
            if expected in values:
                found += 1
            else:
                errors.append(("개인정보 놓침", 0.0, f"{expected}  <-  {s['text']}"))
    print(f"\n[개인정보 마스킹] 항목 {total}개 중 {found}개 탐지   재현율 {found / total:.3f}")

    ordered = sorted(times)
    print(f"\n[검사 시간] 평균 {statistics.mean(times):.2f} ms   "
          f"p95 {ordered[int(len(ordered) * 0.95) - 1]:.2f} ms   최대 {ordered[-1]:.2f} ms  (규칙 단계만, LLM 호출 제외)")

    if args.show_errors:
        print("\n[틀린 표본]")
        for kind, score, text in errors:
            print(f"  {kind:<8} {score:>5.2f}  {text}")


if __name__ == "__main__":
    main()
