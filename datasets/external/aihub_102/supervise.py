"""밤사이 측정 지킴이. overnight.py가 죽거나 멈추면 메모리를 정리하고 다시 띄운다.

python datasets/external/aihub_102/supervise.py
  - overnight.py는 끝난 묶음을 건너뛰므로 다시 띄워도 이어서 돈다.
  - 죽었을 때: 남은 모델 프로세스(llama-server, 측정 중이던 python)를 내리고 다시 시작. 최대 12번.
  - 멈췄을 때: big 폴더의 파일이 45분 동안 하나도 안 바뀌면 멈춘 것으로 보고 같은 처리.
  - 키보드·마우스 입력이 5분 넘게 없으면 화면만 끈다(절전은 아니다). 아무 키나 누르면 다시 켜진다.
  - 도는 동안 PC가 절전으로 들어가지 않게 한다. 설정은 바꾸지 않고, 끝나면 풀린다.
"""
import ctypes
import os
import subprocess
import sys
import time
from ctypes import wintypes
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).parent
BIG = HERE / "big"
NOWIN = getattr(subprocess, "CREATE_NO_WINDOW", 0)
OLLAMA = Path(os.environ["LOCALAPPDATA"]) / "Programs" / "Ollama" / "ollama.exe"
STALL_S = 45 * 60
IDLE_S = 5 * 60


class LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]


def idle_seconds() -> float:
    info = LASTINPUTINFO()
    info.cbSize = ctypes.sizeof(info)
    ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info))
    return (ctypes.windll.kernel32.GetTickCount() - info.dwTime) / 1000


def screen_off() -> None:
    # WM_SYSCOMMAND, SC_MONITORPOWER, 2(끄기)
    ctypes.windll.user32.PostMessageW(0xFFFF, 0x0112, 0xF170, 2)


def log(msg: str) -> None:
    with open(BIG / "status.txt", "a", encoding="utf-8") as fh:
        fh.write(f"{datetime.now():%m-%d %H:%M:%S}  [지킴이] {msg}\n")


def clean_memory(proc: subprocess.Popen | None) -> None:
    if proc and proc.poll() is None:
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=NOWIN)
    for llm in ("exaone3.5:7.8b", "gemma3:4b"):
        subprocess.run([str(OLLAMA), "stop", llm], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=NOWIN, timeout=60)
    for name in ("llama-server.exe", "ollama.exe"):
        subprocess.run(["taskkill", "/IM", name, "/F"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=NOWIN)
    time.sleep(10)


def newest_change() -> float:
    return max((p.stat().st_mtime for p in BIG.rglob("*") if p.is_file()), default=time.time())


def main() -> None:
    BIG.mkdir(exist_ok=True)
    ctypes.windll.kernel32.SetThreadExecutionState(0x80000000 | 0x00000001)
    proc, restarts, screen_is_off = None, 0, False
    while not (BIG / "DONE").exists():
        if proc is None or proc.poll() is not None:
            if proc is not None:
                restarts += 1
                log(f"측정이 꺼졌다(종료 코드 {proc.returncode}). 메모리를 정리하고 다시 시작한다. {restarts}번째")
                clean_memory(None)
            if restarts > 12:
                log("12번 넘게 꺼져서 그만둔다.")
                break
            proc = subprocess.Popen([sys.executable, str(HERE / "overnight.py")], creationflags=NOWIN)
            started = time.time()
        elif time.time() - max(newest_change(), started) > STALL_S:
            restarts += 1
            log(f"45분 동안 진행이 없다. 메모리를 정리하고 다시 시작한다. {restarts}번째")
            clean_memory(proc)
            proc = None
            continue
        idle = idle_seconds()
        if idle > IDLE_S and not screen_is_off:
            screen_off()
            screen_is_off = True
        elif idle < 30:
            screen_is_off = False
        time.sleep(30)
    log("지킴이 끝")
    ctypes.windll.kernel32.SetThreadExecutionState(0x80000000)


if __name__ == "__main__":
    main()
