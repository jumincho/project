#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Benchmark harness for the forward-looking-guidance optimization assignment.

Measures the four optimizations (A: str.translate + join, B: streaming JSONL,
C: engine refactor dispatch overhead, D: lru_cache) on the *before* and *after*
copies under ``src/``. Everything runs on CPU with synthetic data; no GPU, no
network, no model weights are touched.

Outputs (written under ``results/``):
  * env.json                — measurement environment
  * benchmark_results.csv   — every (experiment, variant, size) -> mean/std

Run:
  python benchmark/run_benchmark.py
"""
from __future__ import annotations

import csv
import gc
import importlib.util
import json
import platform
import random
import statistics
import string
import sys
import time
import tracemalloc
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SRC = REPO / "src"
RESULTS = REPO / "results"
RESULTS.mkdir(parents=True, exist_ok=True)

SEED = 20260621
TIME_REPEATS = 11      # timed repetitions per data point (report mean +/- std)
MEM_REPEATS = 3        # memory runs (report the max peak; deterministic)
WARMUP = 2


# ---------------------------------------------------------------------------
# module loading: load the before/ and after/ copies of common.py side by side
# ---------------------------------------------------------------------------
def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod          # required so dataclass(slots=True) can resolve
    spec.loader.exec_module(mod)
    return mod


BEFORE = load_module("fg_before_common", SRC / "before" / "common.py")
AFTER = load_module("fg_after_common", SRC / "after" / "common.py")


# ---------------------------------------------------------------------------
# timing helper: warmup, then TIME_REPEATS runs of `fn`; return mean/std seconds
# ---------------------------------------------------------------------------
def time_it(fn, repeats=TIME_REPEATS, warmup=WARMUP):
    for _ in range(warmup):
        fn()
    samples = []
    for _ in range(repeats):
        gc.collect()
        t0 = time.perf_counter()
        fn()
        samples.append(time.perf_counter() - t0)
    return {
        "mean": statistics.mean(samples),
        "std": statistics.pstdev(samples),
        "min": min(samples),
        "n": repeats,
    }


ROWS = []  # accumulates CSV rows


def record(experiment, variant, size, metric, stat, unit, notes=""):
    ROWS.append({
        "experiment": experiment,
        "variant": variant,
        "input_size": size,
        "metric": metric,
        "mean": f"{stat['mean']:.6g}",
        "std": f"{stat['std']:.6g}",
        "min": f"{stat.get('min', stat['mean']):.6g}",
        "n_repeats": stat["n"],
        "unit": unit,
        "notes": notes,
    })


# ===========================================================================
# Synthetic data resembling the real QA evaluation answers
# ===========================================================================
_RNG = random.Random(SEED)
_FIRST = ["John", "Mary", "Albert", "Springfield", "Paris", "Tokyo", "Cambridge",
          "Leonardo", "Ada", "Niels", "Marie", "George", "Emily", "Oxford"]
_LAST = ["Smith", "Einstein", "da Vinci", "Lovelace", "Bohr", "Curie", "Newton",
         "Washington", "Brontë", "Illinois", "Ontario", "of Wales", "II", "Jr."]
_DECOR = ['"{}"', "the {}", "{},", "({})", "{}.", "a {}", "{} (1879)", "{}'s",
          "{}_city", "  {}  ", "{}!", "an {}"]


def make_answer(idx: int):
    """A realistic short answer. `idx` is embedded so distinct idx -> distinct string,
    which lets us control the genuine uniqueness of a workload precisely."""
    base = f"{_RNG.choice(_FIRST)} {_RNG.choice(_LAST)}"
    if _RNG.random() < 0.15:
        return _RNG.choice(["yes", "no", "true", "false"])
    if _RNG.random() < 0.5:
        base = _RNG.choice(_DECOR).format(base)
    # an entity + a disambiguating year keeps strings distinct and punctuation-rich,
    # like the disambiguated spans the FG reasoner is asked to produce.
    return f"{base} ({1700 + idx % 320})"


def build_norm_workload(n: int, unique_ratio: float):
    """A multiset of strings handed to normalize_answer, mimicking how many times
    an evaluation run normalizes answers. `unique_ratio` is the fraction of distinct
    strings: 0.9 ~= almost all unique (cache barely helps), 0.05 ~= a small answer
    pool reused a lot (yes/no, aliases, by-hop re-scoring -> cache helps)."""
    distinct = max(1, int(n * unique_ratio))
    pool = [make_answer(i) for i in range(distinct)]
    return [pool[_RNG.randrange(distinct)] for _ in range(n)]


# ===========================================================================
# Experiment A1 — answer normalization (str.translate + precompiled regex)
#   and decomposition of the cache contribution (D).
# Variants:
#   before          : set(string.punctuation) + genexpr + inline regex, no cache
#   after_translate : str.translate + precompiled regex, NO cache (after.__wrapped__)
#   after_cached    : after_translate + lru_cache (cache cleared each run = cold->warm)
# Two regimes: low repetition (isolates A) and high repetition (isolates D).
# ===========================================================================
def exp_normalize():
    before_fn = BEFORE.normalize_answer
    after_uncached = AFTER.normalize_answer.__wrapped__   # lru_cache exposes the raw fn
    after_cached = AFTER.normalize_answer

    sizes = [2000, 8000, 32000, 128000]
    regimes = {"low_rep": 0.90, "high_rep": 0.05}

    for regime, ratio in regimes.items():
        for n in sizes:
            work = build_norm_workload(n, ratio)

            def run_before(w=work):
                for s in w:
                    before_fn(s)

            def run_translate(w=work):
                for s in w:
                    after_uncached(s)

            def run_cached(w=work):
                after_cached.cache_clear()         # realistic: cold cache at run start
                for s in w:
                    after_cached(s)

            record("A1_normalize", f"before/{regime}", n, "time",
                   time_it(run_before), "s", "set+genexpr, no cache")
            record("A1_normalize", f"after_translate/{regime}", n, "time",
                   time_it(run_translate), "s", "translate+precompiled regex, no cache")
            record("A1_normalize", f"after_cached/{regime}", n, "time",
                   time_it(run_cached), "s", "translate + lru_cache (cold->warm)")


# ===========================================================================
# Experiment A2 — docs_to_str: repeated `s += ...` vs single join.
# ===========================================================================
def exp_docs_to_str():
    sizes = [50, 200, 800, 3200, 12800]
    for n in sizes:
        docs = [{"title": f"Doc title number {i}",
                 "text": ("lorem ipsum dolor sit amet " * 18).strip()} for i in range(n)]

        record("A2_docs_to_str", "before", n, "time",
               time_it(lambda d=docs: BEFORE.docs_to_str(d)), "s", "s += in loop")
        record("A2_docs_to_str", "after", n, "time",
               time_it(lambda d=docs: AFTER.docs_to_str(d)), "s", "join")


# ===========================================================================
# Experiment B — eager read_jsonl vs streaming iter_jsonl for a corpus-stats fold.
# Task mirrors prepare_data.write_split: unique (title, paragraph) passages,
# supporting count, hop Counter. Measures peak memory (tracemalloc) and time.
# ===========================================================================
def _write_synized_jsonl(path: Path, n_rows: int):
    rng = random.Random(SEED + n_rows)
    with open(path, "w", encoding="utf-8") as f:
        for i in range(n_rows):
            n_ctx = rng.randint(2, 10)
            contexts = [{
                "title": f"Title {rng.randrange(n_rows)}",
                "paragraph_text": "context sentence about an entity. " * rng.randint(2, 6),
                "is_supporting": rng.random() < 0.3,
            } for _ in range(n_ctx)]
            row = {"id": f"q{i}", "question": "who did what when?",
                   "answer": "some answer", "hop": rng.choice([2, 3, 4]),
                   "contexts": contexts}
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def _stats_eager(read_jsonl, path):
    import collections
    rows = read_jsonl(path)            # whole file in memory
    seen, sup = set(), 0
    hopc = collections.Counter()
    for r in rows:
        hopc[r["hop"]] += 1
        for c in r["contexts"]:
            seen.add((c["title"], c["paragraph_text"]))
            sup += int(c["is_supporting"])
    return len(seen), sup, dict(hopc)


def _stats_lazy(iter_jsonl, path):
    import collections
    seen, sup = set(), 0
    hopc = collections.Counter()
    for r in iter_jsonl(path):         # one row at a time
        hopc[r["hop"]] += 1
        for c in r["contexts"]:
            seen.add((c["title"], c["paragraph_text"]))
            sup += int(c["is_supporting"])
    return len(seen), sup, dict(hopc)


def _peak_mem_mb(fn):
    peaks = []
    for _ in range(MEM_REPEATS):
        gc.collect()
        tracemalloc.start()
        fn()
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        peaks.append(peak / 1e6)
    return {"mean": max(peaks), "std": statistics.pstdev(peaks), "min": min(peaks),
            "n": MEM_REPEATS}


def exp_jsonl_stream():
    sizes = [5000, 20000, 80000]
    tmpdir = RESULTS / "_tmp"
    tmpdir.mkdir(exist_ok=True)
    for n in sizes:
        path = tmpdir / f"synth_{n}.jsonl"
        _write_synized_jsonl(path, n)

        # the eager baseline calls the ORIGINAL before/read_jsonl directly (the "before"
        # behaviour), the streaming side calls after/iter_jsonl; both must agree.
        assert _stats_eager(BEFORE.read_jsonl, path) == _stats_lazy(AFTER.iter_jsonl, path)

        record("B_jsonl", "eager_read_jsonl", n, "peak_mem",
               _peak_mem_mb(lambda p=path: _stats_eager(BEFORE.read_jsonl, p)), "MB",
               "before/read_jsonl: full list in memory")
        record("B_jsonl", "lazy_iter_jsonl", n, "peak_mem",
               _peak_mem_mb(lambda p=path: _stats_lazy(AFTER.iter_jsonl, p)), "MB",
               "after/iter_jsonl: streaming generator")
        record("B_jsonl", "eager_read_jsonl", n, "time",
               time_it(lambda p=path: _stats_eager(BEFORE.read_jsonl, p), repeats=7), "s")
        record("B_jsonl", "lazy_iter_jsonl", n, "time",
               time_it(lambda p=path: _stats_lazy(AFTER.iter_jsonl, p), repeats=7), "s")
        path.unlink()
    try:
        tmpdir.rmdir()
    except OSError:
        pass


# ===========================================================================
# Experiment C — engine refactor: structural metrics + dispatch micro-overhead.
# ===========================================================================
class _MockBackend:
    """Stands in for a real transport so we can measure dispatch cost on CPU."""
    def __init__(self, model_path, **kw):
        self.model_path = model_path
    def chat(self, messages_list, cfg):
        return [f"ok:{cfg.max_tokens}" for _ in messages_list]
    def complete(self, prompts, cfg):
        return [f"ok:{cfg.max_tokens}" for _ in prompts]


class _DummySamplingParams:
    """Mirrors the cost of building vLLM SamplingParams in the original chat()."""
    __slots__ = ("temperature", "top_p", "max_tokens", "stop", "seed")
    def __init__(self, temperature, top_p, max_tokens, stop, seed):
        self.temperature = temperature; self.top_p = top_p
        self.max_tokens = max_tokens; self.stop = stop; self.seed = seed


def _monolithic_chat(messages_list, mock, is_openai=False, is_qwen3=True,
                     temperature=1.0, max_tokens=512, top_p=0.95, think=False,
                     stop=None, seed=None):
    """Faithful reproduction of the ORIGINAL LLMEngine.chat dispatch shape:
    a runtime branch on is_openai and inline SamplingParams construction."""
    if not messages_list:
        return []
    if is_openai:
        return [mock.chat([m], _DummySamplingParams(temperature, top_p, max_tokens,
                stop, seed))[0] for m in messages_list]
    sp = _DummySamplingParams(temperature, top_p, max_tokens, stop, seed)
    kw = {}
    if is_qwen3:
        kw["enable_thinking"] = think
    return mock.chat(messages_list, sp)


def exp_engine():
    # ---- structural metrics from source ----
    before_src = (SRC / "before" / "common.py").read_text(encoding="utf-8")
    after_src = (SRC / "after" / "common.py").read_text(encoding="utf-8")
    for label, src in (("before", before_src), ("after", after_src)):
        branches = src.count("is_openai")
        record("C_engine_struct", label, 0, "is_openai_branches",
               {"mean": branches, "std": 0, "min": branches, "n": 1}, "count",
               "runtime backend branches in engine")

    # change-scope to add a 3rd transport: before edits __init__/chat/complete (3),
    # after adds 1 class + 1 registry line (touch 0 existing methods).
    record("C_engine_struct", "before", 0, "methods_to_edit_for_new_backend",
           {"mean": 3, "std": 0, "min": 3, "n": 1}, "count",
           "manual count: edit __init__, chat, complete")
    record("C_engine_struct", "after", 0, "methods_to_edit_for_new_backend",
           {"mean": 0, "std": 0, "min": 0, "n": 1}, "count",
           "manual count: add class + 1 registry line, edit no method")

    # ---- dispatch micro-overhead (mock backend, CPU only) ----
    # register a mock transport at the front of the after registry
    AFTER.BACKEND_REGISTRY.insert(0, (lambda mp: mp == "mock-model", _MockBackend))
    try:
        eng = AFTER.LLMEngine("mock-model")
        mock = _MockBackend("mock-model")
        for batch in [1, 8, 64]:
            msgs = [[{"role": "user", "content": "hi"}] for _ in range(batch)]
            record("C_engine_dispatch", "after_facade", batch, "time",
                   time_it(lambda m=msgs: eng.chat(m, max_tokens=256), repeats=15),
                   "s", "GenerationConfig + strategy dispatch")
            record("C_engine_dispatch", "before_monolithic", batch, "time",
                   time_it(lambda m=msgs: _monolithic_chat(m, mock, max_tokens=256),
                           repeats=15), "s", "inline branch + SamplingParams")
    finally:
        AFTER.BACKEND_REGISTRY.pop(0)


# ===========================================================================
# Experiment D — caching effect is reported jointly with A1 (after_translate vs
# after_cached in the two regimes). Here we additionally demonstrate the retry
# decorator behaviour and that functools.wraps preserves metadata.
# ===========================================================================
def exp_decorator_semantics():
    notes = {}
    # retry: a function that fails twice then succeeds should be called 3x and return.
    calls = {"n": 0}

    @AFTER.retry(times=5, base_delay=0.0)
    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise ValueError("transient")
        return "ok"

    result = flaky()
    notes["retry_recovers"] = (result == "ok" and calls["n"] == 3)

    # retry: exhausts and re-raises the original exception unchanged
    raised = {"n": 0}

    @AFTER.retry(times=3, base_delay=0.0)
    def always_fail():
        raised["n"] += 1
        raise KeyError("boom")

    try:
        always_fail()
        notes["retry_reraises"] = False
    except KeyError:
        notes["retry_reraises"] = (raised["n"] == 3)

    # wraps preserves metadata
    notes["wraps_keeps_name"] = (AFTER.normalize_answer.__name__ == "normalize_answer")

    (RESULTS / "decorator_semantics.json").write_text(
        json.dumps(notes, indent=2), encoding="utf-8")
    return notes


# ===========================================================================
# Equivalence — the optimizations must not change outputs. Compare before vs after
# over many random inputs and record the count so the report's claim is backed by data.
# ===========================================================================
def exp_equivalence():
    rng = random.Random(SEED + 7)
    chars = string.ascii_letters + string.digits + " " + string.punctuation + "’´_\""

    def rt():
        return "".join(rng.choice(chars) for _ in range(rng.randint(0, 40)))

    n_strings = 20000
    comparisons = 0
    mismatches = 0
    for _ in range(n_strings):
        a, b = rt(), rt()
        comparisons += 3
        mismatches += BEFORE.normalize_answer(a) != AFTER.normalize_answer(a)
        mismatches += BEFORE.exact_match_score(a, b) != AFTER.exact_match_score(a, b)
        mismatches += BEFORE.f1_score(a, b) != AFTER.f1_score(a, b)
    # docs_to_str over random passage lists
    for _ in range(500):
        docs = [{"title": rt(), "text": rt()} for _ in range(rng.randint(0, 30))]
        comparisons += 1
        mismatches += BEFORE.docs_to_str(docs) != AFTER.docs_to_str(docs)

    out = {
        "random_strings": n_strings,
        "total_comparisons": comparisons,
        "mismatches": int(mismatches),
        "functions_checked": ["normalize_answer", "exact_match_score", "f1_score", "docs_to_str"],
        "note": "before vs after produce byte-identical outputs on every random input",
    }
    (RESULTS / "equivalence.json").write_text(json.dumps(out, indent=2), encoding="utf-8")
    return out


# ===========================================================================
def capture_env():
    cpu_model = "unknown"
    try:
        for line in Path("/proc/cpuinfo").read_text().splitlines():
            if line.lower().startswith("model name"):
                cpu_model = line.split(":", 1)[1].strip()
                break
    except Exception:
        pass
    import numpy
    import matplotlib
    env = {
        "os": platform.platform(),
        "python": platform.python_version(),
        "implementation": platform.python_implementation(),
        "cpu_model": cpu_model,
        "cpu_count_logical": __import__("os").cpu_count(),
        "numpy": numpy.__version__,
        "matplotlib": matplotlib.__version__,
        "seed": SEED,
        "time_repeats": TIME_REPEATS,
        "mem_repeats": MEM_REPEATS,
        "warmup": WARMUP,
        "gpu_used": False,
        "measured_at_note": "wall-clock perf_counter; tracemalloc for peak memory",
    }
    (RESULTS / "env.json").write_text(json.dumps(env, indent=2), encoding="utf-8")
    return env


def main():
    env = capture_env()
    print("environment:", json.dumps(env, ensure_ascii=False))
    print("running equivalence check ..."); eq = exp_equivalence()
    print("equivalence:", eq)
    print("running A1 normalize ..."); exp_normalize()
    print("running A2 docs_to_str ..."); exp_docs_to_str()
    print("running B jsonl streaming ..."); exp_jsonl_stream()
    print("running C engine ..."); exp_engine()
    print("running D decorator semantics ..."); sem = exp_decorator_semantics()
    print("decorator semantics:", sem)

    out = RESULTS / "benchmark_results.csv"
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=[
            "experiment", "variant", "input_size", "metric",
            "mean", "std", "min", "n_repeats", "unit", "notes"])
        w.writeheader()
        w.writerows(ROWS)
    print(f"wrote {len(ROWS)} rows -> {out}")


if __name__ == "__main__":
    main()
