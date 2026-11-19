"""Build ``summary.csv``, ``summary.md`` and the PNG charts from the JSONL results.

Pilot-friendly: every table and chart states n, and the markdown summary says plainly when n is
too small to conclude anything.
"""

from __future__ import annotations

import csv
import gzip
import json
from collections import defaultdict
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

from bench.harness.paths import BASELINE_JSONL, RUNS_JSONL  # noqa: F401
from bench.harness.runner import INFRA_STATUSES, run_key
from bench.harness.stats import median, wilson_interval
from bench.harness.store import read_rows
from bench.harness.variants import ALL_VARIANTS, CLIENT_TOOL_SEARCH_SUFFIX

# Okabe-Ito: distinguishable under the common colour-vision deficiencies.
PALETTE = {
    "blue": "#0072B2",
    "orange": "#E69F00",
    "green": "#009E73",
    "vermillion": "#D55E00",
    "purple": "#CC79A7",
    "sky": "#56B4E9",
    "grey": "#6B6B6B",
}
BANDS = ["easy", "multi-step", "ambiguous", "policy"]
VARIANT_ORDER = [
    "per-endpoint-10",
    "per-endpoint-25",
    "per-endpoint-50",
    "per-endpoint-100",
    "per-endpoint-100" + CLIENT_TOOL_SEARCH_SUFFIX,
    "toolsets",
    "search-call",
]
VARIANT_COLOR = {
    "per-endpoint-10": PALETTE["sky"],
    "per-endpoint-25": PALETTE["blue"],
    "per-endpoint-50": PALETTE["purple"],
    "per-endpoint-100": PALETTE["vermillion"],
    "per-endpoint-100" + CLIENT_TOOL_SEARCH_SUFFIX: PALETTE["orange"],
    "toolsets": PALETTE["green"],
    "search-call": PALETTE["grey"],
}
MODEL_COLOR = {"haiku": PALETTE["sky"], "sonnet": PALETTE["blue"]}
MIN_N_NOTE = 10  # below this many runs in a cell, no conclusion should be drawn
DPI = 200
FIGSIZE = (6.4, 4.8)  # 1280 x 960 px at 200 dpi, i.e. 2x of a 640 px wide layout
SURFACE = "#FFFFFF"
INK = "#1F1F1F"
MUTED = "#5A5A5A"


# ------------------------------------------------------------------------------- loading
def latest_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One row per run key: the latest one, preferring a finished run over an infrastructure error."""
    best: dict[str, dict[str, Any]] = {}
    for r in rows:
        key = run_key(r["variant"], r["model_requested"], r["task_id"], r["repeat"])
        prev = best.get(key)
        if prev is None or (prev["status"] in INFRA_STATUSES) or r["status"] not in INFRA_STATUSES:
            best[key] = r
    return list(best.values())


def graded(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [r for r in rows if r.get("pass") is not None]


def _ordered(variants: set[str]) -> list[str]:
    return [v for v in VARIANT_ORDER if v in variants] + sorted(variants - set(VARIANT_ORDER))


def _fmt(v: float | None, digits: int = 0) -> str:
    return "" if v is None else f"{v:.{digits}f}"


# ------------------------------------------------------------------------------- tables
def context_table(baseline: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """First-request input tokens per (variant, model), and the part the server adds.

    The no-server run with the same Claude Code configuration is subtracted, so the number is
    what the MCP server (tool definitions plus instructions) adds to the first request.
    """
    groups: dict[tuple[str, str], list[int]] = defaultdict(list)
    for r in baseline:
        if r.get("first_request_input_tokens") is not None and r["status"] not in INFRA_STATUSES:
            groups[(r["variant"], r["model_requested"])].append(r["first_request_input_tokens"])
    out: list[dict[str, Any]] = []
    for (variant, model), vals in sorted(groups.items()):
        cfg = ALL_VARIANTS.get(variant)
        base_name = "none" + (CLIENT_TOOL_SEARCH_SUFFIX if cfg and cfg.client_tool_search else "")
        base_vals = groups.get((base_name, model), [])
        base = median(base_vals)
        first = median(vals)
        out.append(
            {
                "variant": variant,
                "model": model,
                "tool_count": cfg.tool_count if cfg else None,
                "n": len(vals),
                "first_request_tokens": first,
                "no_server_tokens": base,
                "added_tokens": None if base is None or first is None else first - base,
            }
        )
    return out


def cell_stats(rows: list[dict[str, Any]]) -> dict[str, Any]:
    n = len(rows)
    passes = sum(1 for r in rows if r["pass"])
    lo, hi = wilson_interval(passes, n)

    def med(field: str) -> float | None:
        return median([r[field] for r in rows if r.get(field) is not None])

    return {
        "n": n,
        "passes": passes,
        "pass_rate": passes / n if n else None,
        "wilson_lo": lo,
        "wilson_hi": hi,
        "median_input_tokens": med("input_tokens_total"),
        "median_first_request_tokens": med("first_request_input_tokens"),
        "median_wall_ms": med("wall_ms"),
        "median_turns": med("num_turns"),
        "median_cost_usd": med("total_cost_usd"),
        "mean_wrong_calls": (sum(r["wrong_calls"] for r in rows) / n) if n else None,
        "mean_wrong_write_calls": (sum(r["wrong_write_calls"] for r in rows) / n) if n else None,
        "mean_tool_calls": (sum(r["n_tool_calls"] for r in rows) / n) if n else None,
    }


def summary_rows(
    tasks: list[dict[str, Any]], context: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    done = graded(tasks)
    keys = sorted({(r["variant"], r["model_requested"]) for r in done})
    for variant, model in sorted(
        keys,
        key=lambda k: (
            _ordered({k[0]}) and VARIANT_ORDER.index(k[0]) if k[0] in VARIANT_ORDER else 99,
            k[1],
        ),
    ):
        mine = [r for r in done if r["variant"] == variant and r["model_requested"] == model]
        for band in ["all", *BANDS]:
            cell = mine if band == "all" else [r for r in mine if r["band"] == band]
            if cell:
                out.append(
                    {
                        "kind": "task",
                        "variant": variant,
                        "model": model,
                        "band": band,
                        **cell_stats(cell),
                    }
                )
    for c in context:
        out.append(
            {
                "kind": "context_baseline",
                "variant": c["variant"],
                "model": c["model"],
                "band": "",
                "n": c["n"],
                "median_first_request_tokens": c["first_request_tokens"],
                "added_tokens": c["added_tokens"],
            }
        )
    return out


CSV_FIELDS = [
    "kind",
    "variant",
    "model",
    "band",
    "n",
    "passes",
    "pass_rate",
    "wilson_lo",
    "wilson_hi",
    "median_input_tokens",
    "median_first_request_tokens",
    "added_tokens",
    "median_wall_ms",
    "median_turns",
    "median_cost_usd",
    "mean_tool_calls",
    "mean_wrong_calls",
    "mean_wrong_write_calls",
]


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_FIELDS, extrasaction="ignore")
        writer.writeheader()
        for r in rows:
            writer.writerow(
                {
                    k: (
                        ""
                        if r.get(k) is None
                        else (round(r[k], 4) if isinstance(r[k], float) else r[k])
                    )
                    for k in CSV_FIELDS
                }
            )


# --------------------------------------------------------------------------------- charts
def _style(ax: Any) -> None:
    ax.set_facecolor(SURFACE)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color("#9A9A9A")
    ax.tick_params(colors=MUTED, labelsize=9)
    ax.grid(axis="y", color="#E6E6E6", linewidth=0.8)
    ax.set_axisbelow(True)


def _finish(fig: Any, ax: Any, title: str, subtitle: str, path: Path) -> None:
    ax.set_title(title, loc="left", fontsize=12, fontweight="bold", color=INK, pad=22)
    ax.text(0, 1.03, subtitle, transform=ax.transAxes, fontsize=8.5, color=MUTED, va="bottom")
    fig.patch.set_facecolor(SURFACE)
    fig.tight_layout()
    fig.savefig(path, dpi=DPI, facecolor=SURFACE)
    plt.close(fig)


def _label(variant: str) -> str:
    return variant.replace("per-endpoint-", "per-endpoint\n").replace(
        CLIENT_TOOL_SEARCH_SUFFIX, "\n+ tool search"
    )


def _new() -> tuple[Any, Any]:
    fig, ax = plt.subplots(figsize=FIGSIZE)
    _style(ax)
    return fig, ax


def chart_context(context: list[dict[str, Any]], path: Path) -> bool:
    rows = [c for c in context if c["added_tokens"] is not None]
    if not rows:
        return False
    fig, ax = _new()
    models = sorted({c["model"] for c in rows})
    for model in models:
        mine = [c for c in rows if c["model"] == model]
        pe = sorted(
            [
                c
                for c in mine
                if c["variant"].startswith("per-endpoint") and "+" not in c["variant"]
            ],
            key=lambda c: c["tool_count"],
        )
        colour = MODEL_COLOR.get(model, PALETTE["grey"])
        if pe:
            ax.plot(
                [c["tool_count"] for c in pe],
                [c["added_tokens"] for c in pe],
                marker="o",
                markersize=7,
                linewidth=2,
                color=colour,
                label=f"per-endpoint, {model} (n={pe[0]['n']} per point)",
            )
        for ref, style in (("toolsets", "--"), ("search-call", ":")):
            hit = [c for c in mine if c["variant"] == ref]
            if hit:
                y = hit[0]["added_tokens"]
                ax.axhline(y, linestyle=style, linewidth=1.6, color=colour, alpha=0.85)
                offset = 1 if model == models[0] else -1
                ax.annotate(
                    f"{ref}, {model}: {y:,.0f}",
                    (104, y),
                    xytext=(0, 4 if offset > 0 else -11),
                    textcoords="offset points",
                    fontsize=8,
                    color=INK,
                    ha="right",
                )
    ax.set_xlabel("Tools exposed by the per-endpoint server", color=INK, fontsize=10)
    ax.set_ylabel("Input tokens added to the first request", color=INK, fontsize=10)
    ax.set_xlim(0, 105)
    ax.set_xticks([0, 10, 25, 50, 100])
    ax.set_ylim(bottom=0)
    ax.legend(frameon=False, fontsize=8, loc="upper left")
    ax.yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: f"{v:,.0f}"))
    _finish(
        fig,
        ax,
        "What the server costs before the first question",
        "Added vs no server. Dashed: toolsets. Dotted: search-call.",
        path,
    )
    return True


def chart_input_tokens(tasks: list[dict[str, Any]], path: Path) -> bool:
    done = graded(tasks)
    if not done:
        return False
    variants = _ordered({r["variant"] for r in done})
    fig, ax = _new()
    models = sorted({r["model_requested"] for r in done})
    width = 0.8 / len(models)
    for j, model in enumerate(models):
        xs, ys, ns = [], [], []
        for i, v in enumerate(variants):
            vals = [
                r["input_tokens_total"]
                for r in done
                if r["variant"] == v and r["model_requested"] == model
            ]
            if vals:
                xs.append(i + (j - (len(models) - 1) / 2) * width)
                ys.append(median(vals) or 0)
                ns.append(len(vals))
        bars = ax.bar(
            xs, ys, width=width * 0.92, color=MODEL_COLOR.get(model, PALETTE["grey"]), label=model
        )
        for bar, n in zip(bars, ns, strict=True):
            ax.text(
                bar.get_x() + bar.get_width() / 2,
                bar.get_height(),
                f"n={n}",
                ha="center",
                va="bottom",
                fontsize=7.5,
                color=MUTED,
            )
    ax.set_xticks(range(len(variants)))
    ax.set_xticklabels([_label(v) for v in variants], fontsize=8)
    ax.set_ylabel("Median input tokens per task (all requests)", color=INK, fontsize=10)
    ax.yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: f"{v:,.0f}"))
    ax.legend(frameon=False, fontsize=8)
    _finish(
        fig,
        ax,
        "Input tokens per task",
        "Fresh, cached and cache-written input summed over every request of the run.",
        path,
    )
    return True


def chart_pass_rate(tasks: list[dict[str, Any]], path: Path) -> bool:
    done = graded(tasks)
    if not done:
        return False
    variants = _ordered({r["variant"] for r in done})
    bands = [b for b in BANDS if any(r["band"] == b for r in done)]
    fig, ax = _new()
    width = 0.8 / len(variants)
    for j, v in enumerate(variants):
        for i, band in enumerate(bands):
            cell = [r for r in done if r["variant"] == v and r["band"] == band]
            if not cell:
                continue
            n = len(cell)
            p = sum(1 for r in cell if r["pass"]) / n
            lo, hi = wilson_interval(sum(1 for r in cell if r["pass"]), n)
            x = i + (j - (len(variants) - 1) / 2) * width
            ax.bar(
                x,
                p,
                width=width * 0.9,
                color=VARIANT_COLOR.get(v, PALETTE["grey"]),
                label=v if i == 0 else None,
            )
            ax.errorbar(x, p, yerr=[[p - lo], [hi - p]], color=INK, linewidth=1, capsize=2)
            ax.text(x, 1.04, f"{n}", ha="center", fontsize=7, color=MUTED)
    ax.set_xticks(range(len(bands)))
    ax.set_xticklabels(bands, fontsize=9)
    ax.set_ylim(0, 1.12)
    ax.set_ylabel("Pass rate (bars) with 95% Wilson interval", color=INK, fontsize=10)
    ax.yaxis.set_major_formatter(matplotlib.ticker.PercentFormatter(1.0))
    ax.legend(frameon=False, fontsize=7.5, ncol=3, loc="upper center", bbox_to_anchor=(0.5, -0.1))
    _finish(
        fig,
        ax,
        "Pass rate by variant and task band",
        "Small numbers above the bars are n (runs in that cell). All models pooled.",
        path,
    )
    return True


def chart_wall(tasks: list[dict[str, Any]], path: Path) -> bool:
    done = [r for r in graded(tasks) if r.get("wall_ms") is not None]
    if not done:
        return False
    variants = _ordered({r["variant"] for r in done})
    data = [[r["wall_ms"] / 1000 for r in done if r["variant"] == v] for v in variants]
    fig, ax = _new()
    box = ax.boxplot(
        data,
        patch_artist=True,
        widths=0.55,
        medianprops={"color": INK, "linewidth": 1.6},
        whiskerprops={"color": "#8A8A8A"},
        capprops={"color": "#8A8A8A"},
        flierprops={
            "marker": "o",
            "markersize": 4,
            "markerfacecolor": "none",
            "markeredgecolor": "#8A8A8A",
        },
    )
    for patch, v in zip(box["boxes"], variants, strict=True):
        patch.set_facecolor(VARIANT_COLOR.get(v, PALETTE["grey"]))
        patch.set_edgecolor("#4A4A4A")
    ax.set_xticks(range(1, len(variants) + 1))
    ax.set_xticklabels(
        [f"{_label(v)}\nn={len(d)}" for v, d in zip(variants, data, strict=True)], fontsize=8
    )
    ax.set_ylabel("Wall time per task (seconds)", color=INK, fontsize=10)
    _finish(
        fig,
        ax,
        "Wall time per task",
        "Reported by Claude Code: includes its start-up, model time and tool time.",
        path,
    )
    return True


def chart_wrong_calls(tasks: list[dict[str, Any]], path: Path) -> bool:
    amb = [r for r in graded(tasks) if r["band"] == "ambiguous"]
    if not amb:
        return False
    variants = _ordered({r["variant"] for r in amb})
    fig, ax = _new()
    width = 0.38
    for k, (field, label, colour) in enumerate(
        (
            ("wrong_write_calls", "writes outside the task", PALETTE["vermillion"]),
            ("wrong_read_calls", "reads outside the task", PALETTE["sky"]),
        )
    ):
        ys, ns = [], []
        for v in variants:
            cell = [r for r in amb if r["variant"] == v]
            ys.append(sum(r[field] for r in cell) / len(cell))
            ns.append(len(cell))
        xs = [i + (k - 0.5) * width for i in range(len(variants))]
        ax.bar(xs, ys, width=width * 0.92, color=colour, label=label)
    for i, v in enumerate(variants):
        ax.text(
            i,
            ax.get_ylim()[1] * 0.98,
            f"n={sum(1 for r in amb if r['variant'] == v)}",
            ha="center",
            va="top",
            fontsize=8,
            color=MUTED,
        )
    ax.set_xticks(range(len(variants)))
    ax.set_xticklabels([_label(v) for v in variants], fontsize=8)
    ax.set_ylabel("Mean calls per ambiguous-task run", color=INK, fontsize=10)
    ax.legend(frameon=False, fontsize=8, loc="upper left", bbox_to_anchor=(0, 0.93))
    _finish(
        fig,
        ax,
        "Wrong-tool calls on ambiguous tasks",
        "Calls to operations the task does not need (a refused attempt still counts).",
        path,
    )
    return True


def chart_cost(tasks: list[dict[str, Any]], path: Path) -> bool:
    done = [r for r in graded(tasks) if r.get("total_cost_usd") is not None]
    if not done:
        return False
    variants = _ordered({r["variant"] for r in done})
    models = sorted({r["model_requested"] for r in done})
    fig, ax = _new()
    width = 0.8 / len(models)
    for j, model in enumerate(models):
        xs, ys, ns = [], [], []
        for i, v in enumerate(variants):
            vals = [
                r["total_cost_usd"]
                for r in done
                if r["variant"] == v and r["model_requested"] == model
            ]
            if vals:
                xs.append(i + (j - (len(models) - 1) / 2) * width)
                ys.append(1000 * sum(vals) / len(vals))
                ns.append(len(vals))
        bars = ax.bar(
            xs, ys, width=width * 0.92, color=MODEL_COLOR.get(model, PALETTE["grey"]), label=model
        )
        for bar, n in zip(bars, ns, strict=True):
            ax.text(
                bar.get_x() + bar.get_width() / 2,
                bar.get_height(),
                f"n={n}",
                ha="center",
                va="bottom",
                fontsize=7.5,
                color=MUTED,
            )
    ax.set_xticks(range(len(variants)))
    ax.set_xticklabels([_label(v) for v in variants], fontsize=8)
    ax.set_ylabel("CLI-reported cost per 1,000 tasks (USD)", color=INK, fontsize=10)
    ax.yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: f"${v:,.0f}"))
    ax.legend(frameon=False, fontsize=8)
    _finish(
        fig,
        ax,
        "Cost per 1,000 tasks",
        "Mean CLI-reported cost (list-price equivalent) x 1,000. Cache writes included.",
        path,
    )
    return True


CHARTS: list[tuple[str, str]] = [
    ("1-context-tokens-vs-tool-count.png", "context"),
    ("2-input-tokens-per-task.png", "input"),
    ("3-pass-rate-by-band.png", "pass"),
    ("4-wall-time.png", "wall"),
    ("5-wrong-calls-ambiguous.png", "wrong"),
    ("6-cost-per-1000-tasks.png", "cost"),
]


# -------------------------------------------------------------------------------- markdown
def _md_table(header: Sequence[str], rows: Sequence[Sequence[str]]) -> str:
    lines = ["| " + " | ".join(header) + " |", "|" + "|".join("---" for _ in header) + "|"]
    lines += ["| " + " | ".join(r) + " |" for r in rows]
    return "\n".join(lines)


def build_markdown(
    tasks: list[dict[str, Any]], baseline: list[dict[str, Any]], context: list[dict[str, Any]]
) -> str:
    done = graded(tasks)
    statuses: dict[str, int] = defaultdict(int)
    for r in [*tasks, *baseline]:
        statuses[r["status"]] += 1
    out = ["# Benchmark summary", ""]
    out.append(
        f"Task runs: {len(tasks)} ({len(done)} graded). Context baseline runs: {len(baseline)}. "
        "Statuses: " + ", ".join(f"{k}={v}" for k, v in sorted(statuses.items())) + "."
    )
    cell_sizes = [
        len([r for r in done if r["variant"] == v and r["model_requested"] == m])
        for v, m in {(r["variant"], r["model_requested"]) for r in done}
    ]
    if cell_sizes and max(cell_sizes) < MIN_N_NOTE:
        out += [
            "",
            f"**n is too small to conclude anything.** The largest variant x model cell has {max(cell_sizes)} "
            f"task runs (fewer than {MIN_N_NOTE}). Treat the pass rates and medians below as a smoke test of "
            "the harness, not as results.",
        ]
    leaks = [r for r in [*tasks, *baseline] if r.get("isolation_problems")]
    out += [
        "",
        f"Isolation check: {len(leaks)} run(s) had unexpected tools, skills or MCP servers in the init event.",
    ]
    out += ["", "## Context added by the server (first request)", ""]
    if context:
        out.append(
            _md_table(
                [
                    "variant",
                    "model",
                    "n",
                    "first request tokens",
                    "no-server tokens",
                    "added by server",
                ],
                [
                    [
                        c["variant"],
                        c["model"],
                        str(c["n"]),
                        _fmt(c["first_request_tokens"]),
                        _fmt(c["no_server_tokens"]),
                        _fmt(c["added_tokens"]),
                    ]
                    for c in context
                ],
            )
        )
    else:
        out.append("No context baseline runs yet.")
    out += ["", "## Per variant and model (all bands)", ""]
    rows = []
    for variant in _ordered({r["variant"] for r in done}):
        for model in sorted({r["model_requested"] for r in done}):
            cell = [r for r in done if r["variant"] == variant and r["model_requested"] == model]
            if not cell:
                continue
            s = cell_stats(cell)
            rows.append(
                [
                    variant,
                    model,
                    str(s["n"]),
                    f"{s['passes']}/{s['n']} ({s['wilson_lo']:.0%} to {s['wilson_hi']:.0%})",
                    _fmt(s["median_input_tokens"]),
                    _fmt((s["median_wall_ms"] or 0) / 1000, 1),
                    _fmt(s["median_cost_usd"], 4),
                    _fmt(s["mean_wrong_write_calls"], 2),
                ]
            )
    out.append(
        _md_table(
            [
                "variant",
                "model",
                "n",
                "pass (95% Wilson)",
                "median input tokens",
                "median wall s",
                "median cost USD",
                "mean wrong writes",
            ],
            rows,
        )
        if rows
        else "No graded task runs yet."
    )
    failed = [r for r in done if not r["pass"]]
    if failed:
        out += ["", "## Failed runs", ""]
        out.append(
            _md_table(
                ["variant", "model", "task", "reason"],
                [
                    [r["variant"], r["model_requested"], r["task_id"], str(r["grade_reason"])[:90]]
                    for r in failed
                ],
            )
        )
    return "\n".join(out) + "\n"


def tool_lists(baseline: list[dict[str, Any]], raw_dir: Path) -> dict[str, list[str]]:
    """The tools each variant exposed to the model, read from the init event of a baseline stream."""
    out: dict[str, list[str]] = {}
    for r in baseline:
        if r["variant"] in out or r["status"] in INFRA_STATUSES:
            continue
        stream = raw_dir / f"{r['run_id']}.stream.jsonl.gz"
        if not stream.exists():
            continue
        with gzip.open(stream, "rt", encoding="utf-8") as fh:
            for line in fh:
                ev = json.loads(line)
                if ev.get("type") == "system" and ev.get("subtype") == "init":
                    out[r["variant"]] = sorted(ev.get("tools", []))
                    break
    return out


def build_report(results_dir: Path) -> dict[str, Any]:
    tasks = latest_rows(read_rows(results_dir / "runs.jsonl"))
    baseline = latest_rows(read_rows(results_dir / "context_baseline.jsonl"))
    context = context_table(baseline)
    charts_dir = results_dir / "charts"
    charts_dir.mkdir(parents=True, exist_ok=True)
    write_csv(results_dir / "summary.csv", summary_rows(tasks, context))
    (results_dir / "summary.md").write_text(
        build_markdown(tasks, baseline, context), encoding="utf-8"
    )
    lists = tool_lists(baseline, results_dir / "raw")
    if lists:
        (results_dir / "tool_lists.json").write_text(
            json.dumps(lists, indent=1, sort_keys=True) + "\n", encoding="utf-8"
        )
    makers: dict[str, Callable[[Path], bool]] = {
        "context": lambda p: chart_context(context, p),
        "input": lambda p: chart_input_tokens(tasks, p),
        "pass": lambda p: chart_pass_rate(tasks, p),
        "wall": lambda p: chart_wall(tasks, p),
        "wrong": lambda p: chart_wrong_calls(tasks, p),
        "cost": lambda p: chart_cost(tasks, p),
    }
    written = [name for name, key in CHARTS if makers[key](charts_dir / name)]
    return {"charts": written, "tasks": len(tasks), "baseline": len(baseline)}
