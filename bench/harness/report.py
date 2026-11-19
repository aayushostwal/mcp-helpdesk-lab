"""Build ``summary.csv``, ``summary.md`` and the PNG charts from the JSONL results.

Pilot-friendly: every table and chart states n, and the markdown summary says plainly when n is
too small to conclude anything.
"""

from __future__ import annotations

import csv
import gzip
import json
import textwrap
from collections import defaultdict
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

from bench.harness.paths import BASELINE_JSONL, RUNS_JSONL  # noqa: F401
from bench.harness.runner import INFRA_STATUSES, run_key
from bench.harness.stats import median, quantile, wilson_interval
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
FIGSIZE = (6.0, 6.0)  # 1200 x 1200 px at 200 dpi: square, so text stays large on a phone
FLAWED_TASK = "multi-04-merge-duplicate-customers"  # seed flaw, see docs/benchmark.md
CT_VARIANT = "per-endpoint-100" + CLIENT_TOOL_SEARCH_SUFFIX
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
        if any(r["task_id"] == FLAWED_TASK for r in mine):
            out.append(
                {
                    "kind": "task_excluding_task_with_seed_flaw",
                    "variant": variant,
                    "model": model,
                    "band": "all",
                    **cell_stats(exclude_flawed(mine)),
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


def exclude_flawed(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [r for r in rows if r["task_id"] != FLAWED_TASK]


def pass_table(done: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Pass counts per variant and model (and models pooled), with and without the flawed task."""
    out: list[dict[str, Any]] = []
    for variant in _ordered({r["variant"] for r in done}):
        for model in [*sorted({r["model_requested"] for r in done}), "all"]:
            cell = [
                r
                for r in done
                if r["variant"] == variant and (model == "all" or r["model_requested"] == model)
            ]
            if not cell:
                continue
            row: dict[str, Any] = {"variant": variant, "model": model}
            for key, rows in (("all", cell), ("excl", exclude_flawed(cell))):
                n = len(rows)
                k = sum(1 for r in rows if r["pass"])
                lo, hi = wilson_interval(k, n)
                row[key] = {"n": n, "passes": k, "rate": k / n if n else None, "lo": lo, "hi": hi}
            out.append(row)
    return out


def by_task_rows(done: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Per-task pass rates by variant, per model and pooled."""
    out: list[dict[str, Any]] = []
    for task_id in sorted({r["task_id"] for r in done}):
        mine = [r for r in done if r["task_id"] == task_id]
        band = mine[0]["band"]
        for variant in _ordered({r["variant"] for r in mine}):
            for model in [*sorted({r["model_requested"] for r in mine}), "all"]:
                cell = [
                    r
                    for r in mine
                    if r["variant"] == variant and (model == "all" or r["model_requested"] == model)
                ]
                if not cell:
                    continue
                k = sum(1 for r in cell if r["pass"])
                lo, hi = wilson_interval(k, len(cell))
                out.append(
                    {
                        "task_id": task_id,
                        "band": band,
                        "variant": variant,
                        "model": model,
                        "n": len(cell),
                        "passes": k,
                        "pass_rate": k / len(cell),
                        "wilson_lo": lo,
                        "wilson_hi": hi,
                    }
                )
    return out


BY_TASK_FIELDS = [
    "task_id",
    "band",
    "variant",
    "model",
    "n",
    "passes",
    "pass_rate",
    "wilson_lo",
    "wilson_hi",
]


def write_by_task_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=BY_TASK_FIELDS)
        writer.writeheader()
        for r in rows:
            writer.writerow(
                {k: round(r[k], 4) if isinstance(r[k], float) else r[k] for k in BY_TASK_FIELDS}
            )


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
    ax.tick_params(colors=MUTED, labelsize=10)
    ax.grid(axis="y", color="#E6E6E6", linewidth=0.8)
    ax.set_axisbelow(True)


def _finish(fig: Any, ax: Any, title: str, subtitle: str, path: Path) -> None:
    subtitle = "\n".join(textwrap.wrap(subtitle.replace("\n", " "), 64))
    lines = subtitle.count("\n") + 1
    ax.set_title(title, loc="left", fontsize=13, fontweight="bold", color=INK, pad=14 + 14 * lines)
    ax.text(
        0,
        1.02,
        subtitle,
        transform=ax.transAxes,
        fontsize=9.5,
        color=MUTED,
        va="bottom",
        linespacing=1.3,
    )
    fig.patch.set_facecolor(SURFACE)
    fig.tight_layout()
    fig.savefig(path, dpi=DPI, facecolor=SURFACE)
    plt.close(fig)


def _label(variant: str) -> str:
    """Short x tick label: the per-endpoint variants are named by their tool count."""
    if variant.startswith("per-endpoint-"):
        count = variant.removeprefix("per-endpoint-").removesuffix(CLIENT_TOOL_SEARCH_SUFFIX)
        return f"{count}\ntools" + (
            "\n+ tool\nsearch" if CLIENT_TOOL_SEARCH_SUFFIX in variant else ""
        )
    return variant.replace("search-call", "search-\ncall")


VARIANT_AXIS_LABEL = "Tool design (numbers = per-endpoint server with that many tools)"


def _new() -> tuple[Any, Any]:
    fig, ax = plt.subplots(figsize=FIGSIZE)
    _style(ax)
    return fig, ax


def _thousands(ax: Any, axis: str = "y", prefix: str = "") -> None:
    fmt = matplotlib.ticker.FuncFormatter(lambda v, _: f"{prefix}{v:,.0f}")
    (ax.yaxis if axis == "y" else ax.xaxis).set_major_formatter(fmt)


def _grouped_bars(
    ax: Any,
    variants: list[str],
    models: list[str],
    value: Callable[[str, str], float | None],
    text: Callable[[float], str],
    label_y: Callable[[str, str], float | None] | None = None,
) -> None:
    width = 0.8 / len(models)
    top = max(
        [value(v, m) or 0 for v in variants for m in models]
        + [(label_y(v, m) or 0) if label_y else 0 for v in variants for m in models]
    )
    for j, model in enumerate(models):
        xs, ys, tops = [], [], []
        for i, v in enumerate(variants):
            y = value(v, model)
            if y is not None:
                xs.append(i + (j - (len(models) - 1) / 2) * width)
                ys.append(y)
                tops.append(max(y, (label_y(v, model) if label_y else y) or y))
        bars = ax.bar(
            xs,
            ys,
            width=width * 0.92,
            color=MODEL_COLOR.get(model, PALETTE["grey"]),
            label=model,
        )
        for bar, y, ty in zip(bars, ys, tops, strict=True):
            ax.text(
                bar.get_x() + bar.get_width() / 2,
                ty + top * 0.015,
                text(y),
                ha="center",
                va="bottom",
                fontsize=8.5,
                color=INK,
                rotation=90,
            )
    ax.set_ylim(0, top * 1.22)
    ax.set_xticks(range(len(variants)))


def _n_ticks(
    ax: Any, variants: list[str], done: list[dict[str, Any]], per_model: bool = True
) -> None:
    labels = []
    for v in variants:
        sizes = sorted(
            {
                len([r for r in done if r["variant"] == v and r["model_requested"] == m])
                for m in {r["model_requested"] for r in done}
                if per_model
            }
            or {len([r for r in done if r["variant"] == v])}
        )
        n = max(sizes) if sizes else 0
        labels.append(f"{_label(v)}\nn={n}")
    ax.set_xticklabels(labels, fontsize=10)


def chart_context(context: list[dict[str, Any]], path: Path) -> bool:
    rows = [c for c in context if c["added_tokens"] is not None]
    if not rows:
        return False
    fig, ax = _new()
    models = sorted({c["model"] for c in rows})
    ref_style = {"toolsets": ("--", PALETTE["green"]), "search-call": (":", PALETTE["grey"])}
    gap = 0.0
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
        if pe:
            filled = model == models[-1]
            colour = MODEL_COLOR.get(model, PALETTE["grey"])
            ax.plot(
                [c["tool_count"] for c in pe],
                [c["added_tokens"] for c in pe],
                marker="o",
                markersize=9 if not filled else 6,
                markerfacecolor="white" if not filled else colour,
                markeredgewidth=1.8,
                linewidth=2.2 if filled else 0,
                color=colour,
                label=f"per-endpoint, {model} (n={pe[0]['n']} runs per point)",
                zorder=3 if filled else 4,
            )
            if filled:
                for c in pe:
                    ax.annotate(
                        f"{c['added_tokens']:,.0f}",
                        (c["tool_count"], c["added_tokens"]),
                        xytext=(9, -16),
                        textcoords="offset points",
                        fontsize=10,
                        color=INK,
                        ha="left",
                    )
        hit = [c for c in mine if c["variant"] == CT_VARIANT]
        if hit:
            ax.plot(
                [100],
                [hit[0]["added_tokens"]],
                marker="D",
                markersize=8,
                color=PALETTE["orange"],
                linestyle="none",
                zorder=5,
                label="per-endpoint 100 + client tool search" if model == models[0] else None,
            )
    for ref, (style, colour) in ref_style.items():
        vals = {c["model"]: c["added_tokens"] for c in rows if c["variant"] == ref}
        if not vals:
            continue
        gap = max(gap, max(vals.values()) - min(vals.values()))
        ax.axhline(max(vals.values()), linestyle=style, linewidth=2, color=colour, zorder=2)
        both = " / ".join(f"{vals[m]:,.0f} {m}" for m in models if m in vals)
        ax.annotate(
            f"{ref}: {both}",
            (117, max(vals.values())),
            xytext=(0, 5),
            textcoords="offset points",
            fontsize=10,
            color=INK,
            ha="right",
        )
    ax.set_xlabel("Tools exposed by the per-endpoint server", color=INK, fontsize=11)
    ax.set_ylabel("Input tokens the server adds to the first request", color=INK, fontsize=11)
    ax.set_xlim(0, 118)
    ax.set_xticks([10, 25, 50, 100])
    ax.set_ylim(bottom=0)
    ax.legend(frameon=False, fontsize=9.5, loc="upper left", bbox_to_anchor=(0, 0.93))
    _thousands(ax)
    _finish(
        fig,
        ax,
        "What the server costs before the first question",
        "Added vs the same Claude Code with no server. Dashed: toolsets. Dotted: search-call. "
        f"Haiku and sonnet agree within {gap + 5:,.0f} tokens, so the markers overlap.",
        path,
    )
    return True


def chart_input_tokens(tasks: list[dict[str, Any]], path: Path) -> bool:
    done = graded(tasks)
    if not done:
        return False
    variants = _ordered({r["variant"] for r in done})
    models = sorted({r["model_requested"] for r in done})
    fig, ax = _new()

    def val(v: str, m: str) -> float | None:
        return median(
            [
                r["input_tokens_total"]
                for r in done
                if r["variant"] == v and r["model_requested"] == m
            ]
        )

    _grouped_bars(ax, variants, models, val, lambda y: f"{y / 1000:,.0f}k")
    _n_ticks(ax, variants, done)
    ax.set_xlabel("Tool design; n = runs per bar", color=INK, fontsize=10.5)
    ax.set_ylabel("Median input tokens per task (all requests)", color=INK, fontsize=11)
    _thousands(ax)
    ax.legend(frameon=False, fontsize=10, loc="upper right")
    _finish(
        fig,
        ax,
        "Input tokens per task",
        "Fresh, cached and cache-written input summed over every request of the run.\n"
        "All graded runs, including failed ones.",
        path,
    )
    return True


def _pass_cells(done: list[dict[str, Any]], variant: str, band: str) -> tuple[int, int]:
    cell = [r for r in done if r["variant"] == variant and r["band"] == band]
    return sum(1 for r in cell if r["pass"]), len(cell)


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
            k, n = _pass_cells(done, v, band)
            if not n:
                continue
            lo, hi = wilson_interval(k, n)
            p = k / n
            x = i + (j - (len(variants) - 1) / 2) * width
            ax.bar(
                x,
                p,
                width=width * 0.9,
                color=VARIANT_COLOR.get(v, PALETTE["grey"]),
                label=_legend_name(v) if i == 0 else None,
            )
            ax.errorbar(x, p, yerr=[[p - lo], [hi - p]], color=INK, linewidth=1, capsize=1.5)
    ticks = []
    for band in bands:
        sizes = sorted({_pass_cells(done, v, band)[1] for v in variants} - {0})
        ticks.append(f"{band}\nn={sizes[-1]}" + (f" ({sizes[0]})" if len(sizes) > 1 else ""))
    ax.set_xticks(range(len(bands)))
    ax.set_xticklabels(ticks, fontsize=10)
    ax.set_ylim(0, 1.05)
    ax.set_ylabel("Pass rate, with 95% Wilson interval", color=INK, fontsize=11)
    ax.yaxis.set_major_formatter(matplotlib.ticker.PercentFormatter(1.0))
    ax.legend(frameon=False, fontsize=9, ncol=2, loc="upper center", bbox_to_anchor=(0.5, -0.16))
    _finish(
        fig,
        ax,
        "Pass rate by variant and task band",
        "All tasks, models pooled. n = runs per bar; in brackets for per-endpoint-10, which skips\n"
        "tasks it cannot solve. The multi-step band includes multi-04 (a task with a seed flaw).",
        path,
    )
    return True


def _legend_name(v: str) -> str:
    return v.replace(CLIENT_TOOL_SEARCH_SUFFIX, " + tool search")


def _pass_by_variant_model(
    done: list[dict[str, Any]], variant: str, model: str, *, exclude: str | None = None
) -> tuple[int, int]:
    cell = [
        r
        for r in done
        if r["variant"] == variant and r["model_requested"] == model and r["task_id"] != exclude
    ]
    return sum(1 for r in cell if r["pass"]), len(cell)


def chart_pass_overall(tasks: list[dict[str, Any]], path: Path) -> bool:
    done = graded(tasks)
    if not done:
        return False
    variants = _ordered({r["variant"] for r in done})
    models = sorted({r["model_requested"] for r in done})
    fig, axes = plt.subplots(
        1, 2, figsize=(FIGSIZE[0], FIGSIZE[1]), sharey=True, gridspec_kw={"wspace": 0.25}
    )
    panels = [("All tasks", None), ("Excluding task\nwith seed flaw", FLAWED_TASK)]
    for ax, (title, exclude) in zip(axes, panels, strict=True):
        _style(ax)
        ax.grid(axis="x", color="#E6E6E6", linewidth=0.8)
        ax.grid(axis="y", visible=False)
        for i, v in enumerate(variants):
            for j, m in enumerate(models):
                k, n = _pass_by_variant_model(done, v, m, exclude=exclude)
                if not n:
                    continue
                lo, hi = wilson_interval(k, n)
                y = i + (j - (len(models) - 1) / 2) * 0.28
                colour = MODEL_COLOR.get(m, PALETTE["grey"])
                ax.plot([lo, hi], [y, y], color=colour, linewidth=2.2, solid_capstyle="butt")
                ax.plot(
                    k / n,
                    y,
                    "o",
                    color=colour,
                    markersize=6.5,
                    clip_on=False,
                    label=m if i == 0 else None,
                )
        ax.set_xlim(0.7, 1.02)
        ax.xaxis.set_major_formatter(matplotlib.ticker.PercentFormatter(1.0, decimals=0))
        ax.set_xticks([0.7, 0.8, 0.9, 1.0])
        ax.set_xticklabels(["70", "80", "90", "100"])
        ax.set_title(title, fontsize=10.5, color=INK, loc="left")
        ax.tick_params(axis="x", labelsize=9.5)
    axes[0].set_yticks(range(len(variants)))
    axes[0].set_yticklabels(
        [
            _label(v)
            .replace("search-\ncall", "search-call")
            .replace("\n+", " +")
            .replace("\n", " ")
            if "tool\nsearch" not in _label(v)
            else "100 tools\n+ tool search"
            for v in variants
        ],
        fontsize=10,
    )
    axes[0].set_ylim(len(variants) - 0.5, -0.5)
    axes[0].set_ylabel("")
    handles, names = axes[0].get_legend_handles_labels()
    fig.legend(
        handles,
        names,
        frameon=False,
        fontsize=10,
        loc="upper right",
        ncol=1,
        bbox_to_anchor=(0.99, 0.93),
    )
    fig.text(
        0.5,
        0.01,
        "Pass rate, %, with 95% Wilson interval. Axis starts at 70%, not 0.",
        ha="center",
        fontsize=9.5,
        color=MUTED,
    )
    fig.suptitle(
        "Pass rate per variant and model",
        x=0.02,
        ha="left",
        fontsize=13,
        fontweight="bold",
        color=INK,
    )
    sizes = sorted({_pass_by_variant_model(done, v, m)[1] for v in variants for m in models})
    sizes_ex = sorted(
        {
            _pass_by_variant_model(done, v, m, exclude=FLAWED_TASK)[1]
            for v in variants
            for m in models
        }
    )
    fig.text(
        0.02,
        0.885,
        f"n per point: {', '.join(map(str, sizes))} (all tasks); "
        f"{', '.join(map(str, sizes_ex))} (excluding the flawed task).\n"
        "Intervals overlap almost everywhere.",
        fontsize=9.5,
        color=MUTED,
        va="bottom",
    )
    fig.patch.set_facecolor(SURFACE)
    fig.subplots_adjust(left=0.25, right=0.97, top=0.78, bottom=0.12)
    fig.savefig(path, dpi=DPI, facecolor=SURFACE)
    plt.close(fig)
    return True


def chart_wall(tasks: list[dict[str, Any]], path: Path) -> bool:
    done = [r for r in graded(tasks) if r.get("wall_ms") is not None]
    if not done:
        return False
    variants = _ordered({r["variant"] for r in done})
    models = sorted({r["model_requested"] for r in done})
    fig, ax = _new()
    spread: dict[tuple[str, str], tuple[float, float]] = {}

    def val(v: str, m: str) -> float | None:
        vals = [
            r["wall_ms"] / 1000 for r in done if r["variant"] == v and r["model_requested"] == m
        ]
        if not vals:
            return None
        spread[(v, m)] = (quantile(vals, 0.25) or 0, quantile(vals, 0.75) or 0)
        return median(vals)

    width = 0.8 / len(models)
    for j, model in enumerate(models):
        xs, ys, lo, hi = [], [], [], []
        for i, v in enumerate(variants):
            y = val(v, model)
            if y is None:
                continue
            xs.append(i + (j - (len(models) - 1) / 2) * width)
            ys.append(y)
            lo.append(y - spread[(v, model)][0])
            hi.append(spread[(v, model)][1] - y)
        ax.bar(
            xs,
            ys,
            width=width * 0.92,
            color=MODEL_COLOR.get(model, PALETTE["grey"]),
            label=model,
        )
        ax.errorbar(xs, ys, yerr=[lo, hi], fmt="none", ecolor=INK, elinewidth=1.2, capsize=3)
        for x, y, h in zip(xs, ys, hi, strict=True):
            ax.text(x, y + h + 0.2, f"{y:.1f}", ha="center", va="bottom", fontsize=9, color=INK)
    ax.set_xticks(range(len(variants)))
    _n_ticks(ax, variants, done)
    ax.set_xlabel("Tool design; n = runs per bar", color=INK, fontsize=10.5)
    ax.set_ylabel("Wall time per task (seconds)", color=INK, fontsize=11)
    ax.set_ylim(bottom=0)
    ax.legend(frameon=False, fontsize=10, loc="upper left")
    slowest = max(done, key=lambda r: r["wall_ms"])
    _finish(
        fig,
        ax,
        "Wall time per task",
        "Bar = median, whisker = 25th to 75th percentile. Claude Code's own wall clock.\n"
        f"Slowest single run: {slowest['wall_ms'] / 1000:.0f} s ({slowest['model_requested']}, "
        f"{slowest['variant']}, {slowest['task_id']}), off the scale of the whiskers.",
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
    top = 0.0
    for k, (field, label, colour) in enumerate(
        (
            ("wrong_write_calls", "writes outside the task", PALETTE["vermillion"]),
            ("wrong_read_calls", "reads outside the task", PALETTE["sky"]),
        )
    ):
        ys = []
        for v in variants:
            cell = [r for r in amb if r["variant"] == v]
            ys.append(sum(r[field] for r in cell) / len(cell))
        top = max(top, *ys)
        xs = [i + (k - 0.5) * width for i in range(len(variants))]
        ax.bar(xs, ys, width=width * 0.92, color=colour, label=label)
        for x, y in zip(xs, ys, strict=True):
            ax.text(
                x,
                y + 0.008,
                f"{y:.2f}",
                ha="center",
                va="bottom",
                fontsize=8.5,
                color=INK,
                rotation=90,
            )
    ax.set_ylim(0, top * 1.25)
    ax.set_xticks(range(len(variants)))
    ax.set_xticklabels(
        [f"{_label(v)}\nn={sum(1 for r in amb if r['variant'] == v)}" for v in variants],
        fontsize=10,
    )
    ax.set_xlabel("Tool design; n = runs per design", color=INK, fontsize=10.5)
    ax.set_ylabel("Mean calls per ambiguous-task run", color=INK, fontsize=11)
    ax.legend(frameon=False, fontsize=10, loc="upper left")
    _finish(
        fig,
        ax,
        "Wrong-tool calls on ambiguous tasks",
        "Calls to operations the task does not need; a refused attempt still counts.\n"
        "Both models pooled. All graded runs of the ambiguous band.",
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

    def vals(v: str, m: str) -> list[float]:
        return [
            r["total_cost_usd"] for r in done if r["variant"] == v and r["model_requested"] == m
        ]

    _grouped_bars(
        ax,
        variants,
        models,
        lambda v, m: median(vals(v, m)),
        lambda y: f"${y:.4f}",
    )
    _n_ticks(ax, variants, done)
    ax.set_xlabel("Tool design; n = runs per bar", color=INK, fontsize=10.5)
    ax.set_ylabel("CLI-reported cost per task (USD)", color=INK, fontsize=11)
    ax.yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: f"${v:.3f}"))
    ax.legend(frameon=False, fontsize=10, loc="upper left")
    _finish(
        fig,
        ax,
        "Cost per task",
        "Median per task. List-price equivalent, as reported by Claude Code;\n"
        "not a bill. Means are in KEY_NUMBERS.md.",
        path,
    )
    return True


CHARTS: list[tuple[str, str]] = [
    ("1-context-tokens-vs-tool-count.png", "context"),
    ("2-input-tokens-per-task.png", "input"),
    ("3-pass-rate-by-band.png", "pass"),
    ("4-wall-time.png", "wall"),
    ("5-wrong-calls-ambiguous.png", "wrong"),
    ("6-cost-per-task.png", "cost"),
    ("7-pass-rate-by-variant.png", "overall"),
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
    regraded = sum(1 for r in tasks if r.get("regraded_from"))
    if regraded:
        out += [
            "",
            f"{regraded} run(s) carry a post-hoc re-grade (`regraded_from`); the latest grade is "
            "counted. See docs/benchmark.md, section Post-hoc changes and known task flaws.",
        ]
    out += ["", "## Pass rates: all tasks and excluding the task with a seed flaw", ""]
    out.append(
        "multi-04-merge-duplicate-customers has a flaw in its seed data (see docs/benchmark.md). "
        "The second pair of columns drops it. Intervals are 95% Wilson; most overlap."
    )
    out.append("")
    if done:
        out.append(
            _md_table(
                [
                    "variant",
                    "model",
                    "all tasks: passed/n",
                    "all tasks: 95% interval",
                    "excluding task with seed flaw: passed/n",
                    "excluding task with seed flaw: 95% interval",
                ],
                [
                    [
                        t["variant"],
                        t["model"],
                        f"{t['all']['passes']}/{t['all']['n']} ({t['all']['rate']:.0%})",
                        f"{t['all']['lo']:.0%} to {t['all']['hi']:.0%}",
                        f"{t['excl']['passes']}/{t['excl']['n']} ({t['excl']['rate']:.0%})"
                        if t["excl"]["n"]
                        else "",
                        f"{t['excl']['lo']:.0%} to {t['excl']['hi']:.0%}" if t["excl"]["n"] else "",
                    ]
                    for t in pass_table(done)
                ],
            )
        )
        out += ["", "Per-task pass rates by variant: `summary_by_task.csv`."]
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


def _pct(v: float | None) -> str:
    return "" if v is None else f"{v:.1%}"


def build_key_numbers(
    tasks: list[dict[str, Any]],
    baseline: list[dict[str, Any]],
    context: list[dict[str, Any]],
    probe: list[dict[str, Any]] | None = None,
) -> str:
    """Plain tables of the headline numbers, all computed from the same rows as summary.md."""
    done = graded(tasks)
    variants = _ordered({r["variant"] for r in done})
    models = sorted({r["model_requested"] for r in done})
    out = ["# Key numbers", ""]
    out += [
        "Generated by `python -m bench report` from `runs.jsonl` (latest grade per run) and "
        "`context_baseline.jsonl`. Nothing here is edited by hand.",
        "",
        "## Context tokens added to the first request (median over baseline runs)",
        "",
        _md_table(
            [
                "variant",
                "model",
                "baseline runs",
                "first request tokens",
                "no-server tokens",
                "added",
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
        ),
        "",
        "## Median input tokens per task (all requests of a run)",
        "",
    ]
    rows = []
    for v in variants:
        for m in models:
            cell = [r for r in done if r["variant"] == v and r["model_requested"] == m]
            if cell:
                rows.append([v, m, str(len(cell)), _fmt(cell_stats(cell)["median_input_tokens"])])
    out += [_md_table(["variant", "model", "n", "median input tokens"], rows), ""]
    out += ["## Pass rates (95% Wilson interval)", ""]
    out.append(
        _md_table(
            [
                "variant",
                "model",
                "all tasks passed/n",
                "all tasks rate",
                "all tasks interval",
                "excluding task with seed flaw passed/n",
                "excluding task with seed flaw rate",
                "excluding task with seed flaw interval",
            ],
            [
                [
                    t["variant"],
                    t["model"],
                    f"{t['all']['passes']}/{t['all']['n']}",
                    _pct(t["all"]["rate"]),
                    f"{t['all']['lo']:.1%} to {t['all']['hi']:.1%}",
                    f"{t['excl']['passes']}/{t['excl']['n']}",
                    _pct(t["excl"]["rate"]),
                    f"{t['excl']['lo']:.1%} to {t['excl']['hi']:.1%}",
                ]
                for t in pass_table(done)
            ],
        )
    )
    out += ["", "## Median wall time per task, seconds", ""]
    rows = []
    for v in variants:
        for m in models:
            cell = [r for r in done if r["variant"] == v and r["model_requested"] == m]
            if cell:
                wall = cell_stats(cell)["median_wall_ms"]
                rows.append([v, m, str(len(cell)), _fmt(None if wall is None else wall / 1000, 2)])
    out += [_md_table(["variant", "model", "n", "median wall s"], rows), ""]
    out += ["## Median CLI-reported cost per task, USD (list-price equivalent)", ""]
    rows = []
    for v in variants:
        for m in models:
            cell = [r for r in done if r["variant"] == v and r["model_requested"] == m]
            if cell:
                rows.append([v, m, str(len(cell)), _fmt(cell_stats(cell)["median_cost_usd"], 4)])
    for m in models:
        cell = [r for r in done if r["model_requested"] == m]
        rows.append(
            ["all variants", m, str(len(cell)), _fmt(cell_stats(cell)["median_cost_usd"], 4)]
        )
    out += [_md_table(["variant", "model", "n", "median cost USD"], rows), ""]
    out += ["## Wrong-write calls (writes the task does not need; refused attempts count)", ""]
    rows = []
    for v in variants:
        for m in models:
            cell = [r for r in done if r["variant"] == v and r["model_requested"] == m]
            if cell:
                bad = [r for r in cell if r["wrong_write_calls"] > 0]
                rows.append(
                    [
                        v,
                        m,
                        str(len(cell)),
                        str(sum(r["wrong_write_calls"] for r in cell)),
                        str(len(bad)),
                        str(sum(1 for r in bad if not r["pass"])),
                    ]
                )
    out += [
        _md_table(
            [
                "variant",
                "model",
                "n",
                "wrong-write calls",
                "runs with at least one",
                "of those, failed runs",
            ],
            rows,
        ),
        "",
    ]
    out += ["## Tool search variant (per-endpoint-100 with and without client tool search)", ""]
    rows = []
    for v in ("per-endpoint-100", CT_VARIANT):
        for m in models:
            cell = [r for r in done if r["variant"] == v and r["model_requested"] == m]
            added = next(
                (c["added_tokens"] for c in context if c["variant"] == v and c["model"] == m), None
            )
            if cell:
                st = cell_stats(cell)
                rows.append(
                    [
                        v,
                        m,
                        str(st["n"]),
                        _fmt(added),
                        _fmt(st["median_input_tokens"]),
                        f"{st['passes']}/{st['n']}",
                        f"{st['wilson_lo']:.1%} to {st['wilson_hi']:.1%}",
                        _fmt((st["median_wall_ms"] or 0) / 1000, 2),
                        _fmt(st["median_cost_usd"], 4),
                    ]
                )
    out += [
        _md_table(
            [
                "variant",
                "model",
                "n",
                "context tokens added",
                "median input tokens",
                "passed/n",
                "95% interval",
                "median wall s",
                "median cost USD",
            ],
            rows,
        ),
        "",
    ]
    if probe:
        out += ["## Tool search probe (sonnet, first request tokens)", ""]
        out.append(
            _md_table(
                ["probe tools", "ENABLE_TOOL_SEARCH", "first request tokens", "tools listed"],
                [
                    [
                        str(r["probe_tools"]),
                        str(r["probe_env"]),
                        _fmt(r["first_request_input_tokens"]),
                        str(r["init_tool_count"]),
                    ]
                    for r in probe
                    if r["status"] not in INFRA_STATUSES
                ],
            )
        )
        out.append("")
    return "\n".join(out)


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
    write_by_task_csv(results_dir / "summary_by_task.csv", by_task_rows(graded(tasks)))
    (results_dir / "KEY_NUMBERS.md").write_text(
        build_key_numbers(
            tasks, baseline, context, read_rows(results_dir / "tool_search_probe.jsonl")
        ),
        encoding="utf-8",
    )
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
        "overall": lambda p: chart_pass_overall(tasks, p),
        "wrong": lambda p: chart_wrong_calls(tasks, p),
        "cost": lambda p: chart_cost(tasks, p),
    }
    written = [name for name, key in CHARTS if makers[key](charts_dir / name)]
    return {"charts": written, "tasks": len(tasks), "baseline": len(baseline)}
