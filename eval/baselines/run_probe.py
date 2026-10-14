"""탐침 표본을 우리 규칙, 카카오 Kanana Safeguard-Prompt, 삼성SDS SGuard-JailbreakFilter에 돌려 비교한다.

python -m eval.baselines.run_probe          (저장소 루트에서, transformers 필요)
결과는 eval/baselines/probe_result.json 에 남는다.
"""
from __future__ import annotations

import gc
import json
import statistics
import sys
import time
from collections import defaultdict
from pathlib import Path

import torch
from transformers import AutoModelForCausalLM, AutoTokenizer

from app import guard
from app.config import Settings
from eval.baselines.probe_samples import ATTACK, SAMPLES

HERE = Path(__file__).parent


def run_rules() -> tuple[list[bool], list[float], float]:
    settings, flags, times = Settings(), [], []
    for _, _, text in SAMPLES:
        started = time.perf_counter()
        result = guard.inspect([{"role": "user", "content": text}], settings)
        times.append((time.perf_counter() - started) * 1000)
        flags.append(result.decision != "allow")
    return flags, times, 0.0


def _load(model_id: str):
    tok = AutoTokenizer.from_pretrained(model_id)
    model = AutoModelForCausalLM.from_pretrained(model_id, dtype=torch.float16, device_map="cuda").eval()
    return tok, model


def _free(model) -> float:
    vram = torch.cuda.max_memory_allocated() / 2**30
    del model
    gc.collect()
    torch.cuda.empty_cache()
    torch.cuda.reset_peak_memory_stats()
    return round(vram, 2)


def _allow_explicit_head_dim() -> None:
    """Kanana는 hidden 1792, head 24개, head_dim 128을 따로 지정한 구조다.

    transformers 5.x의 설정 검증은 hidden이 head 수로 나누어떨어져야 한다고 보고 로딩을 막는다.
    모델 코드 자체는 head_dim을 지원하므로 그 검증 하나만 끈다.
    """
    from transformers import LlamaConfig

    def skip(self):
        return None

    skip.__name__ = "validate_architecture"
    LlamaConfig.validate_architecture = skip
    validators = getattr(LlamaConfig, "__class_validators__", None)
    if validators is not None:
        LlamaConfig.__class_validators__ = [v for v in validators if v.__name__ != "validate_architecture"]


def run_kanana() -> tuple[list[bool], list[float], float]:
    _allow_explicit_head_dim()
    tok, model = _load("kakaocorp/kanana-safeguard-prompt-2.1b")
    flags, times = [], []
    for i, (_, _, text) in enumerate([SAMPLES[0], *SAMPLES]):  # 첫 건은 예열
        started = time.perf_counter()
        ids = tok.apply_chat_template([{"role": "user", "content": text}], tokenize=True,
                                      return_tensors="pt", return_dict=True).to(model.device)
        with torch.no_grad():
            out = model.generate(**ids, max_new_tokens=1, pad_token_id=tok.eos_token_id)
        torch.cuda.synchronize()
        label = tok.decode(out[0][ids["input_ids"].shape[-1]], skip_special_tokens=False)
        if i:
            times.append((time.perf_counter() - started) * 1000)
            flags.append("UNSAFE" in label)
    return flags, times, _free(model)


def run_sguard() -> tuple[list[bool], list[float], float]:
    tok, model = _load("SamsungSDS-Research/SGuard-JailbreakFilter-2B-v1")
    vocab = tok.get_vocab()
    safe_id, unsafe_id = vocab["safe"], vocab["unsafe"]
    flags, times = [], []
    for i, (_, _, text) in enumerate([SAMPLES[0], *SAMPLES]):
        started = time.perf_counter()
        ids = tok.apply_chat_template([{"role": "user", "content": text}], add_generation_prompt=True,
                                      tokenize=True, return_dict=True, return_tensors="pt").to(model.device)
        with torch.inference_mode():
            out = model.generate(**ids, max_new_tokens=1, do_sample=False,
                                 return_dict_in_generate=True, output_logits=True)
        torch.cuda.synchronize()
        logits = out.logits[0][0].float()
        prob_unsafe = torch.softmax(torch.stack([logits[safe_id], logits[unsafe_id]]), dim=0)[1].item()
        if i:
            times.append((time.perf_counter() - started) * 1000)
            flags.append(prob_unsafe >= 0.6)  # 모델 카드의 기본 임계값
    return flags, times, _free(model)


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    runners = {"우리 규칙": run_rules, "Kanana Safeguard-Prompt": run_kanana, "SGuard-JailbreakFilter": run_sguard}
    results = {name: fn() for name, fn in runners.items()}

    cats: dict[str, list[int]] = defaultdict(list)
    for i, (cat, _, _) in enumerate(SAMPLES):
        cats[cat].append(i)

    names = list(results)
    print(f"{'유형':<22}{'건수':>4}  " + "  ".join(f"{n:>24}" for n in names))
    table = {}
    for cat, idx in cats.items():
        is_attack = SAMPLES[idx[0]][1] == ATTACK
        row = {n: sum(results[n][0][i] for i in idx) for n in names}
        table[cat] = {"n": len(idx), "kind": "탐지" if is_attack else "오탐", **row}
        print(f"{cat:<22}{len(idx):>4}  " + "  ".join(
            f"{('탐지 ' if is_attack else '오탐 ') + str(row[n]) + '/' + str(len(idx)):>24}" for n in names))

    print("\n지연 시간(ms, 중앙값)과 GPU 메모리(GB)")
    perf = {}
    for n in names:
        _, times, vram = results[n]
        perf[n] = {"median_ms": round(statistics.median(times), 2), "max_ms": round(max(times), 2), "vram_gb": vram}
        print(f"  {n:<26} {perf[n]['median_ms']:>8} ms   최대 {perf[n]['max_ms']:>8} ms   {vram or '-':>5} GB")

    print("\n틀린 표본 (O = 탐지함, . = 통과시킴)")
    rows = []
    for i, (cat, label, text) in enumerate(SAMPLES):
        marks = {n: bool(results[n][0][i]) for n in names}
        rows.append({"cat": cat, "label": label, "text": text, **marks})
        if any(m != bool(label) for m in marks.values()):
            line = text.replace("\n", " / ")[:70]
            print(f"  [{cat}] " + " ".join("O" if marks[n] else "." for n in names) + f"  {line}")

    (HERE / "probe_result.json").write_text(
        json.dumps({"models": names, "by_category": table, "perf": perf, "rows": rows}, ensure_ascii=False, indent=1),
        encoding="utf-8")


if __name__ == "__main__":
    main()
