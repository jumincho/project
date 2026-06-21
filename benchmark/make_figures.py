#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Render benchmark figures from results/benchmark_results.csv into results/figures/.

Axis labels are kept in English so the plots render without a CJK font in matplotlib;
the report prose around them is Korean.
"""
from __future__ import annotations

import csv
from collections import defaultdict
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

REPO = Path(__file__).resolve().parent.parent
CSV = REPO / "results" / "benchmark_results.csv"
FIG = REPO / "results" / "figures"
FIG.mkdir(parents=True, exist_ok=True)

ROWS = list(csv.DictReader(open(CSV, encoding="utf-8")))


def pick(exp, metric=None):
    return [r for r in ROWS if r["experiment"] == exp and (metric is None or r["metric"] == metric)]


plt.rcParams.update({"figure.dpi": 150, "font.size": 11, "axes.grid": True,
                     "grid.alpha": 0.3, "axes.axisbelow": True})


# --- Fig 1: normalize time vs n, two regimes, three variants ---------------
def fig_normalize_time():
    d = defaultdict(dict)
    for r in pick("A1_normalize"):
        var, regime = r["variant"].split("/")
        d[(regime, var)][int(r["input_size"])] = float(r["mean"]) * 1e3  # ms
    fig, axes = plt.subplots(1, 2, figsize=(10, 4), sharey=True)
    names = {"before": "before (set+genexpr)", "after_translate": "after: translate",
             "after_cached": "after: translate+lru_cache"}
    colors = {"before": "#c0392b", "after_translate": "#e67e22", "after_cached": "#27ae60"}
    for ax, regime, title in [(axes[0], "low_rep", "Low repetition (~90% unique)"),
                              (axes[1], "high_rep", "High repetition (~5% unique)")]:
        for var in ["before", "after_translate", "after_cached"]:
            xs = sorted(d[(regime, var)])
            ys = [d[(regime, var)][x] for x in xs]
            ax.plot(xs, ys, marker="o", label=names[var], color=colors[var])
        ax.set_xscale("log"); ax.set_yscale("log")
        ax.set_title(title); ax.set_xlabel("workload size n (normalize calls)")
    axes[0].set_ylabel("time (ms, log)")
    axes[0].legend(fontsize=8, loc="upper left")
    fig.suptitle("Answer normalization: str.translate (A) and lru_cache (D)")
    fig.tight_layout()
    fig.savefig(FIG / "fig1_normalize_time.png"); plt.close(fig)


# --- Fig 2: speedup factors at the largest n -------------------------------
def fig_normalize_speedup():
    d = defaultdict(dict)
    nmax = max(int(r["input_size"]) for r in pick("A1_normalize"))
    for r in pick("A1_normalize"):
        if int(r["input_size"]) != nmax:
            continue
        var, regime = r["variant"].split("/")
        d[regime][var] = float(r["mean"])
    fig, ax = plt.subplots(figsize=(6, 4))
    regimes = ["low_rep", "high_rep"]
    transl = [d[g]["before"] / d[g]["after_translate"] for g in regimes]
    cached = [d[g]["before"] / d[g]["after_cached"] for g in regimes]
    x = range(len(regimes)); w = 0.35
    b1 = ax.bar([i - w/2 for i in x], transl, w, label="translate (A)", color="#e67e22")
    b2 = ax.bar([i + w/2 for i in x], cached, w, label="translate+cache (A+D)", color="#27ae60")
    for bars in (b1, b2):
        for b in bars:
            ax.text(b.get_x()+b.get_width()/2, b.get_height(), f"{b.get_height():.1f}x",
                    ha="center", va="bottom", fontsize=9)
    ax.set_xticks(list(x)); ax.set_xticklabels(["low repetition", "high repetition"])
    ax.set_ylabel("speedup vs before (x)")
    ax.set_title(f"Normalization speedup at n={nmax:,}")
    ax.legend()
    fig.tight_layout(); fig.savefig(FIG / "fig2_normalize_speedup.png"); plt.close(fig)


# --- Fig 3: JSONL peak memory + time ---------------------------------------
def fig_jsonl():
    mem = defaultdict(dict); tim = defaultdict(dict)
    for r in pick("B_jsonl", "peak_mem"):
        mem[r["variant"]][int(r["input_size"])] = float(r["mean"])
    for r in pick("B_jsonl", "time"):
        tim[r["variant"]][int(r["input_size"])] = float(r["mean"]) * 1e3
    fig, axes = plt.subplots(1, 2, figsize=(10, 4))
    names = {"eager_read_jsonl": "eager read_jsonl (list)",
             "lazy_iter_jsonl": "lazy iter_jsonl (stream)"}
    colors = {"eager_read_jsonl": "#c0392b", "lazy_iter_jsonl": "#27ae60"}
    for var in names:
        xs = sorted(mem[var]); axes[0].plot(xs, [mem[var][x] for x in xs], marker="o",
                                            label=names[var], color=colors[var])
        xs = sorted(tim[var]); axes[1].plot(xs, [tim[var][x] for x in xs], marker="s",
                                            label=names[var], color=colors[var])
    axes[0].set_title("Peak memory (corpus-stats fold)")
    axes[0].set_xlabel("rows in JSONL"); axes[0].set_ylabel("peak memory (MB)")
    axes[1].set_title("Wall-clock time")
    axes[1].set_xlabel("rows in JSONL"); axes[1].set_ylabel("time (ms)")
    axes[0].legend(fontsize=9); axes[1].legend(fontsize=9)
    fig.suptitle("Streaming JSONL (B): memory flat vs growing")
    fig.tight_layout(); fig.savefig(FIG / "fig3_jsonl.png"); plt.close(fig)


# --- Fig 4: docs_to_str (honest near-tie) ----------------------------------
def fig_docs():
    d = defaultdict(dict)
    for r in pick("A2_docs_to_str"):
        d[r["variant"]][int(r["input_size"])] = float(r["mean"]) * 1e3
    fig, ax = plt.subplots(figsize=(6, 4))
    for var, c in (("before", "#c0392b"), ("after", "#27ae60")):
        xs = sorted(d[var]); ax.plot(xs, [d[var][x] for x in xs], marker="o",
                                     label=f"{var} ({'s+=' if var=='before' else 'join'})", color=c)
    ax.set_xscale("log"); ax.set_yscale("log")
    ax.set_xlabel("passages concatenated (n)"); ax.set_ylabel("time (ms, log)")
    ax.set_title("docs_to_str: join vs s+= (both ~ linear on CPython)")
    ax.legend()
    fig.tight_layout(); fig.savefig(FIG / "fig4_docs_to_str.png"); plt.close(fig)


# --- Fig 5: engine structural metrics + dispatch overhead ------------------
def fig_engine():
    struct = defaultdict(dict)
    for r in pick("C_engine_struct"):
        struct[r["metric"]][r["variant"]] = float(r["mean"])
    disp = defaultdict(dict)
    for r in pick("C_engine_dispatch"):
        disp[r["variant"]][int(r["input_size"])] = float(r["mean"]) * 1e6  # us
    fig, axes = plt.subplots(1, 2, figsize=(10, 4))
    metrics = ["is_openai_branches", "methods_to_edit_for_new_backend"]
    labels = ["is_openai branches", "methods to edit\nto add a backend"]
    x = range(len(metrics)); w = 0.35
    bef = [struct[m]["before"] for m in metrics]; aft = [struct[m]["after"] for m in metrics]
    axes[0].bar([i-w/2 for i in x], bef, w, label="before", color="#c0392b")
    axes[0].bar([i+w/2 for i in x], aft, w, label="after", color="#27ae60")
    for i, m in enumerate(metrics):
        axes[0].text(i-w/2, struct[m]["before"], int(struct[m]["before"]), ha="center", va="bottom")
        axes[0].text(i+w/2, struct[m]["after"], int(struct[m]["after"]), ha="center", va="bottom")
    axes[0].set_xticks(list(x)); axes[0].set_xticklabels(labels, fontsize=9)
    axes[0].set_ylabel("count (lower is better)")
    axes[0].set_title("Structural metrics"); axes[0].legend()

    for var, c in (("before_monolithic", "#c0392b"), ("after_facade", "#27ae60")):
        xs = sorted(disp[var]); axes[1].plot(xs, [disp[var][x] for x in xs], marker="o",
                                             label=var, color=c)
    axes[1].set_xlabel("batch size"); axes[1].set_ylabel("dispatch time (us)")
    axes[1].set_title("Dispatch overhead (mock backend)")
    axes[1].legend(fontsize=9)
    fig.suptitle("Engine refactor (C): structure vs micro-overhead")
    fig.tight_layout(); fig.savefig(FIG / "fig5_engine.png"); plt.close(fig)


def main():
    fig_normalize_time(); fig_normalize_speedup(); fig_jsonl(); fig_docs(); fig_engine()
    print("figures ->", FIG)
    for p in sorted(FIG.glob("*.png")):
        print("  ", p.name)


if __name__ == "__main__":
    main()
