"""밤사이 측정. big/c1~c5의 질문을 개발용·봉인용 차례로, 모델 두 종으로 잰다.

python datasets/external/aihub_102/overnight.py
  - 묶음 하나가 끝날 때마다 결과 엑셀이 그 묶음 폴더에 남는다. 이미 결과가 있는 묶음은 건너뛴다.
  - 실패하면 같은 묶음을 최대 3번 다시 돌린다.
  - 도는 동안 PC가 절전으로 들어가지 않게 한다(설정은 바꾸지 않고, 끝나면 풀린다).
  - 진행 상황은 big/status.txt, 다 끝나면 big/DONE 이 생긴다.
게이트웨이의 규칙·응답 검사는 건드리지 않는다. 문장과 답은 화면에 찍지 않는다.
"""
import ctypes
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).parent
ROOT = HERE.parents[2]
BIG = HERE / "big"
LLMS = ["exaone3.5:7.8b", "gemma3:4b"]
OLLAMA = Path(os.environ["LOCALAPPDATA"]) / "Programs" / "Ollama" / "ollama.exe"
NOWIN = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def log(msg: str) -> None:
    with open(BIG / "status.txt", "a", encoding="utf-8") as fh:
        fh.write(f"{datetime.now():%m-%d %H:%M:%S}  {msg}\n")


def unload_models() -> None:
    """탐지 모델을 GPU에 올리기 전에 Ollama에 떠 있는 LLM을 내린다."""
    for llm in LLMS:
        subprocess.run([str(OLLAMA), "stop", llm], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=NOWIN)
    time.sleep(5)


def wait_for_first_run() -> None:
    """먼저 돌려 둔 gemma3 150건 측정이 끝날 때까지 기다린다(최대 40분)."""
    for _ in range(240):
        if list((HERE / "gemma3").glob("측정결과_*봉인용.xlsx")):
            return
        time.sleep(10)
    log("gemma3 150건 측정이 40분 안에 끝나지 않았다. 기다리지 않고 시작한다.")


def main() -> None:
    BIG.mkdir(exist_ok=True)
    ctypes.windll.kernel32.SetThreadExecutionState(0x80000000 | 0x00000001)  # 절전 방지
    log("시작. 먼저 돌던 측정을 기다린다.")
    wait_for_first_run()
    time.sleep(20)
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    failed = []
    for chunk in sorted(BIG.glob("c*")):
        for split in ("개발용", "봉인용"):
            if list(chunk.glob(f"측정결과_*{split}.xlsx")):
                log(f"{chunk.name} {split}: 이미 있음, 건너뜀")
                continue
            for attempt in (1, 2, 3):
                unload_models()
                log(f"{chunk.name} {split}: 시작 ({attempt}번째)")
                with open(chunk / f"run_{split}.log", "w", encoding="utf-8") as out:
                    code = subprocess.run(
                        [sys.executable, "-u", "-m", "eval.leak.run_baseline", "--file", str(chunk / "평가셋_AIHub_S2.xlsx"),
                         "--split", split, "--llm", *LLMS],
                        cwd=ROOT, env=env, stdout=out, stderr=subprocess.STDOUT, creationflags=NOWIN).returncode
                if code == 0 and list(chunk.glob(f"측정결과_*{split}.xlsx")):
                    log(f"{chunk.name} {split}: 끝")
                    break
                log(f"{chunk.name} {split}: 실패 (종료 코드 {code})")
            else:
                failed.append(f"{chunk.name} {split}")
    unload_models()
    subprocess.run(["taskkill", "/IM", "ollama.exe", "/F"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=NOWIN)
    log("전부 끝" + (f". 실패한 묶음: {', '.join(failed)}" if failed else ""))
    (BIG / "DONE").write_text("\n".join(failed), encoding="utf-8")
    ctypes.windll.kernel32.SetThreadExecutionState(0x80000000)


if __name__ == "__main__":
    main()
