"""Qwen3-TTS throughput benchmark: Base model cloning a designed voice, batched.

uv run python bench/bench.py --model 1.7B-Base-bf16 --voice ru_male --batches 1 4 8
Appends a JSON line per (model, batch) to bench/out/results.jsonl and writes wavs
to bench/out/<model>/<voice>/b<batch>/seg<i>.wav (+ .txt) for listening / ASR checks.
"""
import argparse
import json
import os
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
os.environ.setdefault("HF_HOME", str(ROOT / "models" / "hf"))

import mlx.core as mx  # noqa: E402
import numpy as np  # noqa: E402
import soundfile as sf  # noqa: E402
from mlx_audio.tts.utils import load_model  # noqa: E402

from texts import PROSE_EN, PROSE_RU, TRAPS_EN, TRAPS_RU  # noqa: E402

MEM_LIMIT_GB = 12   # MLX allocator soft limit; memguard.py enforces the hard cap outside
CACHE_LIMIT_GB = 2  # stop MLX's buffer cache from growing without bound


def token_budget(texts):
    """Codec runs at 12.5 tokens/s; ~12+ chars/s of speech -> ~1 token/char. 1.5x headroom
    stops a sequence that never emits EOS from generating 4096 tokens (runaway memory)."""
    return max(int(len(t) * 1.5) + 60 for t in texts)


def run(model, texts, batch, ref_audio, ref_text, lang):
    """Returns (wall seconds, list of np audio in input order, sample rate)."""
    audios = [None] * len(texts)
    sr = None
    t0 = time.perf_counter()
    for start in range(0, len(texts), batch):
        chunk = texts[start:start + batch]
        if batch == 1:
            res = list(model.generate(text=chunk[0], ref_audio=ref_audio, ref_text=ref_text,
                                      lang_code=lang, split_pattern="",
                                      max_tokens=token_budget(chunk)))
            audios[start] = np.concatenate([np.array(r.audio) for r in res])
            sr = res[0].sample_rate
        else:
            for r in model.batch_generate(texts=chunk, ref_audio=ref_audio, ref_text=ref_text,
                                          lang_code=lang, stream=False,
                                          max_tokens=token_budget(chunk)):
                audios[start + r.sequence_idx] = np.array(r.audio)
                sr = r.sample_rate
        mx.clear_cache()
    return time.perf_counter() - t0, audios, sr


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="1.7B-Base-bf16")
    ap.add_argument("--voice", default="ru_male")
    ap.add_argument("--cand", default="cand0")
    ap.add_argument("--batches", type=int, nargs="+", default=[1, 4, 8])
    ap.add_argument("--repeat", type=int, default=2, help="text list repetitions (8 segments each)")
    ap.add_argument("--tag", default="", help="label for concurrent-process runs")
    ap.add_argument("--set", choices=["prose", "traps"], default="prose")
    args = ap.parse_args()

    lang = "russian" if args.voice.startswith("ru") else "english"
    pool = {("prose", "russian"): PROSE_RU, ("prose", "english"): PROSE_EN,
            ("traps", "russian"): TRAPS_RU, ("traps", "english"): TRAPS_EN}[(args.set, lang)]
    texts = pool * args.repeat
    vdir = ROOT / "voices" / args.voice
    ref_audio = str(vdir / f"{args.cand}.wav")
    ref_text = (vdir / "ref.txt").read_text(encoding="utf-8").strip()

    mx.set_memory_limit(int(MEM_LIMIT_GB * 1e9))
    mx.set_cache_limit(int(CACHE_LIMIT_GB * 1e9))
    t_load = time.perf_counter()
    model = load_model(f"mlx-community/Qwen3-TTS-12Hz-{args.model}")
    t_load = time.perf_counter() - t_load
    run(model, texts[:1], 1, ref_audio, ref_text, lang)  # warm-up

    out_root = ROOT / "bench" / "out"
    for b in args.batches:
        mx.reset_peak_memory()
        wall, audios, sr = run(model, texts, b, ref_audio, ref_text, lang)
        dur = sum(len(a) for a in audios) / sr
        rec = dict(model=args.model, voice=args.voice, batch=b, segments=len(texts), tag=args.tag,
                   audio_s=round(dur, 1), wall_s=round(wall, 1), rtf=round(wall / dur, 3),
                   x_realtime=round(dur / wall, 2), mlx_peak_gb=round(mx.get_peak_memory() / 1e9, 2),
                   load_s=round(t_load, 1))
        print(json.dumps(rec), flush=True)
        with open(out_root / "results.jsonl", "a") as f:
            f.write(json.dumps(rec) + "\n")
        wdir = out_root / args.model / args.voice / f"b{b}{args.tag}"
        wdir.mkdir(parents=True, exist_ok=True)
        for i, (a, t) in enumerate(zip(audios[:8], texts[:8])):
            sf.write(wdir / f"seg{i}.wav", a, sr)
            (wdir / f"seg{i}.txt").write_text(t, encoding="utf-8")


if __name__ == "__main__":
    main()
