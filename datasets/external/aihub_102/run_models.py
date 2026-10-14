"""같은 1,150건(S2 고객기록 상황)을 OpenAI 호환 서버(NPU 등)의 모델별로 잰다. 팀장 측정과 조건이 같다.

python datasets/external/aihub_102/run_models.py --url http://서버:8000/v1 --llm 모델이름 [모델이름2 ...]
  - 조건: 같은 질문 1,150건(첫 150 + big/c1~c5), 같은 시스템 문장·고객 기록, temperature 0, max_tokens 500
  - 입력 탐지기는 돌리지 않는다(--skip-detectors). 문장이 같아 결과도 같다.
  - 결과는 npu/<모델이름>/<묶음>/ 아래. 끝난 묶음은 건너뛰므로 끊겨도 같은 명령으로 이어서 돈다.
  - 진행 상황은 npu/status.txt, 모델 하나가 다 끝나면 npu/<모델이름>/DONE
  - --limit 3 을 붙이면 묶음마다 3건씩만 재 본다(시험용, 결과는 npu_test/ 에 따로).
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import httpx

HERE = Path(__file__).parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT))
from app.scenarios import load_scenarios  # noqa: E402
from eval.leak.run_baseline import chat_url  # noqa: E402

SOURCES = {"first": HERE / "평가셋_AIHub_S2.xlsx", **{f"c{i}": HERE / "big" / f"c{i}" / "평가셋_AIHub_S2.xlsx" for i in range(1, 6)}}


def folder_name(llm: str) -> str:
    return "".join("_" if c in ':/\\*?"<>|' else c for c in llm)


def log(out: Path, msg: str) -> None:
    line = f"{datetime.now():%m-%d %H:%M:%S}  {msg}"
    print(line, flush=True)
    with open(out / "status.txt", "a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def preflight(url: str, key: str, llm: str) -> str | None:
    """측정과 같은 형식(system 두 개 + user)으로 한 번 보내 본다. 문제가 있으면 그 내용을 돌려준다."""
    messages = load_scenarios()["S2"].messages("배송은 보통 며칠 걸리나요?")
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    try:
        r = httpx.post(url, headers=headers, timeout=600,
                       json={"model": llm, "messages": messages, "temperature": 0, "max_tokens": 500})
    except httpx.HTTPError as exc:
        return f"서버에 닿지 않음: {exc}"
    if r.status_code != 200:
        return f"서버가 거부함 ({r.status_code}): {r.text[:300]}"
    try:
        text = r.json()["choices"][0]["message"]["content"]
    except (ValueError, KeyError, IndexError):
        return f"응답 형식이 OpenAI 호환이 아님: {r.text[:300]}"
    return None if text and text.strip() else "빈 응답"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True, help="OpenAI 호환 서버 주소 (예: http://서버:8000/v1)")
    parser.add_argument("--llm", nargs="+", required=True, help="서버에 등록된 모델 이름 그대로")
    parser.add_argument("--api-key", default=os.environ.get("LLM_API_KEY", ""))
    parser.add_argument("--limit", type=int, default=0, help="묶음마다 앞 N건만 (시험용)")
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    url = chat_url(args.url)
    out_root = HERE / ("npu_test" if args.limit else "npu")
    out_root.mkdir(exist_ok=True)
    env = dict(os.environ, PYTHONIOENCODING="utf-8", LLM_API_KEY=args.api_key)

    for llm in args.llm:
        base = out_root / folder_name(llm)
        base.mkdir(exist_ok=True)
        problem = preflight(url, args.api_key, llm)
        if problem:
            # 메시지 구조를 바꿔 맞추면 팀장 측정과 조건이 달라진다. 고치지 말고 알린다.
            log(out_root, f"{llm}: 시작 못 함 - {problem}")
            continue
        log(out_root, f"{llm}: 시작")
        started, failed = time.time(), []
        for name, src in SOURCES.items():
            folder = base / name
            folder.mkdir(exist_ok=True)
            target = folder / "평가셋_AIHub_S2.xlsx"
            if not target.exists():
                shutil.copy(src, target)
            for split in ("개발용", "봉인용"):
                if list(folder.glob(f"측정결과_*{split}.xlsx")):
                    continue
                cmd = [sys.executable, "-u", "-m", "eval.leak.run_baseline", "--file", str(target), "--split", split,
                       "--skip-detectors", "--llm", llm, "--url", url]
                if args.limit:
                    cmd += ["--limit", str(args.limit)]
                for attempt in (1, 2, 3):
                    t0 = time.time()
                    with open(folder / f"run_{split}.log", "w", encoding="utf-8") as fh:
                        code = subprocess.run(cmd, cwd=ROOT, env=env, stdout=fh, stderr=subprocess.STDOUT).returncode
                    if code == 0 and list(folder.glob(f"측정결과_*{split}.xlsx")):
                        log(out_root, f"{llm} {name} {split}: 끝 ({(time.time() - t0) / 60:.1f}분)")
                        break
                    log(out_root, f"{llm} {name} {split}: 실패 {attempt}번째 (종료 코드 {code}, {folder / f'run_{split}.log'} 참고)")
                else:
                    failed.append(f"{name} {split}")
        took = (time.time() - started) / 60
        log(out_root, f"{llm}: 전부 끝 ({took:.0f}분)" + (f". 실패한 묶음: {', '.join(failed)}" if failed else ""))
        if not failed:
            (base / "DONE").write_text(f"{took:.0f}분\n", encoding="utf-8")


if __name__ == "__main__":
    main()
