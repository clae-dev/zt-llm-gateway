"""같은 1,150건을 EXAONE 작은 모델(2.4B)로 한 번 더 잰다. 크기에 따라 유출이 달라지는지 보려는 것이다.

python datasets/external/aihub_102/run_small.py
  - 모델이 없으면 먼저 내려받는다(끊기면 이어받는다).
  - 입력 탐지기는 다시 돌리지 않는다. 문장이 같아 결과도 같다.
  - 결과는 small/ 아래에 따로 저장한다. 앞선 결과 파일을 덮어쓰지 않는다.
  - 끝난 묶음은 건너뛴다. 진행 상황은 small/status.txt, 다 끝나면 small/DONE.
"""
import ctypes
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
OUT = HERE / "small"
LLM = "exaone3.5:2.4b"
OLLAMA = Path(os.environ["LOCALAPPDATA"]) / "Programs" / "Ollama" / "ollama.exe"
NOWIN = getattr(subprocess, "CREATE_NO_WINDOW", 0)
SOURCES = {"first": HERE / "평가셋_AIHub_S2.xlsx", **{f"c{i}": HERE / "big" / f"c{i}" / "평가셋_AIHub_S2.xlsx" for i in range(1, 6)}}


def log(msg: str) -> None:
    with open(OUT / "status.txt", "a", encoding="utf-8") as fh:
        fh.write(f"{datetime.now():%m-%d %H:%M:%S}  {msg}\n")


def ensure_ollama() -> None:
    try:
        httpx.get("http://127.0.0.1:11434/api/version", timeout=3)
    except httpx.HTTPError:
        subprocess.Popen([str(OLLAMA), "serve"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=NOWIN)
        time.sleep(8)


def have_model() -> bool:
    try:
        tags = httpx.get("http://127.0.0.1:11434/api/tags", timeout=10).json()
        return any(m["name"] == LLM for m in tags.get("models", []))
    except (httpx.HTTPError, ValueError):
        return False


def main() -> None:
    OUT.mkdir(exist_ok=True)
    ctypes.windll.kernel32.SetThreadExecutionState(0x80000000 | 0x00000001)
    log("시작")
    for attempt in range(1, 21):
        ensure_ollama()
        if have_model():
            break
        log(f"모델 내려받기 ({attempt}번째)")
        subprocess.run([str(OLLAMA), "pull", LLM], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=NOWIN)
    else:
        log("모델을 받지 못했다. 그만둔다.")
        return
    log("모델 준비됨")
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    failed = []
    for name, src in SOURCES.items():
        folder = OUT / name
        folder.mkdir(exist_ok=True)
        target = folder / "평가셋_AIHub_S2.xlsx"
        if not target.exists():
            shutil.copy(src, target)
        for split in ("개발용", "봉인용"):
            if list(folder.glob(f"측정결과_*{split}.xlsx")):
                continue
            for attempt in (1, 2, 3):
                ensure_ollama()
                log(f"{name} {split}: 시작 ({attempt}번째)")
                with open(folder / f"run_{split}.log", "w", encoding="utf-8") as out:
                    code = subprocess.run(
                        [sys.executable, "-u", "-m", "eval.leak.run_baseline", "--file", str(target), "--split", split,
                         "--skip-detectors", "--llm", LLM],
                        cwd=ROOT, env=env, stdout=out, stderr=subprocess.STDOUT, creationflags=NOWIN).returncode
                if code == 0 and list(folder.glob(f"측정결과_*{split}.xlsx")):
                    log(f"{name} {split}: 끝")
                    break
                log(f"{name} {split}: 실패 (종료 코드 {code})")
            else:
                failed.append(f"{name} {split}")
    subprocess.run([str(OLLAMA), "stop", LLM], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=NOWIN)
    subprocess.run(["taskkill", "/IM", "ollama.exe", "/F"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=NOWIN)
    log("전부 끝" + (f". 실패한 묶음: {', '.join(failed)}" if failed else ""))
    (OUT / "DONE").write_text("\n".join(failed), encoding="utf-8")
    ctypes.windll.kernel32.SetThreadExecutionState(0x80000000)


if __name__ == "__main__":
    main()
