"""시나리오(요청을 보낼 '상황') 폴더를 읽는다. 평가(eval/leak)와 시연(콘솔, scripts/demo.py)이 같은 상황을 쓴다."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .config import ROOT

SCENARIO_DIR = ROOT / "datasets" / "eval" / "scenarios"
DEMO_CASES = ROOT / "datasets" / "demo" / "cases.tsv"


@dataclass(frozen=True)
class Scenario:
    id: str
    name: str
    system: str
    context: str
    secrets: dict[str, str]

    def messages(self, request: str) -> list[dict[str, str]]:
        msgs = [{"role": "system", "content": self.system}]
        if self.context:
            msgs.append({"role": "system", "content": "[참고 자료]\n" + self.context})
        return [*msgs, {"role": "user", "content": request}]


def load_scenarios(root: Path = SCENARIO_DIR) -> dict[str, Scenario]:
    out = {}
    for folder in sorted(root.iterdir()):
        if not folder.is_dir() or folder.name.startswith("_"):
            continue
        sid, _, name = folder.name.partition("_")
        secrets = {}
        for line in (folder / "secrets.tsv").read_text(encoding="utf-8").splitlines():
            if "\t" in line:
                key, value = line.split("\t", 1)
                secrets[key.strip()] = value.strip()
        out[sid] = Scenario(sid, name.replace("_", " "),
                            (folder / "system.txt").read_text(encoding="utf-8").strip(),
                            (folder / "context.txt").read_text(encoding="utf-8").strip(), secrets)
    return out


def load_demo_cases(path: Path = DEMO_CASES) -> dict[str, list[dict[str, str]]]:
    """시나리오별 시연 문장. 한 줄에 '시나리오<탭>이름<탭>문장', #으로 시작하면 주석."""
    out: dict[str, list[dict[str, str]]] = {}
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        parts = line.split("\t")
        if line.startswith("#") or len(parts) != 3:
            continue
        sid, name, text = (p.strip() for p in parts)
        out.setdefault(sid, []).append({"name": name, "text": text})
    return out
