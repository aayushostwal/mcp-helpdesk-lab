"""Self-contained HTML pages for the screenshots. Pure functions: records in, HTML text out."""

from __future__ import annotations

import html
from typing import Any

from demo.setups import SETUPS

# Reference palette slots (blue, orange, violet) and surfaces; see the dataviz skill.
INK, INK2, MUTED, SURFACE, RULE = "#0b0b0b", "#52514e", "#6b6a66", "#fcfcfb", "#dcdad4"
C_READ, C_WRITE, C_ERROR = "#2a78d6", "#eb6834", "#6250d6"
KIND_COLOR = {"read": C_READ, "write": C_WRITE, "error": C_ERROR}
ANSWER_LIMIT = 1500
TEXT_LIMIT = 400
MAX_CALLS_SHOWN = 30

e = html.escape


def page(title: str, body: str, css: str = "", width: int = 1200) -> str:
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        f"<title>{e(title)}</title><meta name='viewport' content='width={width}'>"
        f"<style>*{{box-sizing:border-box}}body{{margin:0;width:{width}px}}{css}</style>"
        f"</head><body>{body}</body></html>"
    )


def truncate(text: str, limit: int) -> str:
    """Cut ``text`` at ``limit`` characters with a visible marker; never rewrites what is kept."""
    if len(text) <= limit:
        return text
    return f"{text[:limit]} ... [truncated, {len(text) - limit} more characters]"


def secs(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.1f}"


def fmt_ms(ms: int | None) -> str:
    if ms is None:
        return "?"
    return f"{ms} ms" if ms < 1000 else f"{ms / 1000:.1f} s"


def setup_label(setup: str) -> str:
    return f"{setup} ({SETUPS[setup].tool_count} tools)"


# ---------------------------------------------------------------------------------- terminal
TERMINAL_CSS = """
body{background:#0d1117;color:#e6edf3;font:26px/1.45 ui-monospace,Menlo,'SF Mono',monospace;padding:40px 44px}
.hdr{color:#8b949e;font-size:22px;border-bottom:1px solid #30363d;padding-bottom:14px;margin-bottom:22px}
.prompt{color:#7ee787;white-space:pre-wrap;margin-bottom:22px}
.prompt b{color:#e6edf3;font-weight:600}
.t{color:#8b949e}
.call{white-space:pre-wrap;word-break:break-word;margin-top:10px}
.name{font-weight:700}
.args{color:#a5b1c2}
.res{color:#8b949e;margin-left:34px}
.text{color:#c9d1d9;font-style:italic;white-space:pre-wrap;margin-top:12px}
.gap{color:#8b949e;margin:14px 0;text-align:center;border-top:1px dashed #30363d;border-bottom:1px dashed #30363d;padding:6px 0}
.final{margin-top:26px;border-top:1px solid #30363d;padding-top:18px}
.final .lbl{color:#8b949e;font-size:22px}
.final .ans{white-space:pre-wrap;color:#e6edf3;margin-top:6px}
.foot{margin-top:24px;padding-top:14px;border-top:1px solid #30363d;font-size:23px}
.pass{color:#3fb950;font-weight:700}.fail{color:#ff7b72;font-weight:700}
.k-read{color:#79c0ff}.k-write{color:#ffa657}.k-error{color:#d2a8ff}
"""


def _call_lines(calls: list[dict[str, Any]]) -> list[str]:
    out: list[str] = []
    for c in calls:
        at = "?" if c["start_ms"] is None else f"{c['start_ms'] / 1000:.1f}s"
        kind = c["kind"]
        label = c["tool"] + (
            f" {c['operation_id']}" if c["tool"] == "call_api" and c["operation_id"] else ""
        )
        args = c["args"]
        out.append(
            f"<div class='call'><span class='t'>+{e(at)}</span> <span class='name k-{kind}'>"
            f"&#9679; {e(label)}</span> <span class='args'>{e(args)}</span></div>"
            f"<div class='res k-{kind}'>&#9492; {'error' if kind == 'error' else 'ok'}, "
            f"{e(fmt_ms(c['ms']))}{' (write)' if kind == 'write' else ''}</div>"
        )
    return out


def terminal_page(record: dict[str, Any]) -> str:
    """A dark terminal-style transcript of one real run."""
    calls: list[dict[str, Any]] = record["calls"]
    texts = [t for t in record["agent_text"] if t["text"].strip() != record["final_answer"].strip()]
    lines: list[tuple[int, str]] = []  # (sort key ms, html)
    call_html = _call_lines(calls)
    shown = list(range(len(calls)))
    if len(calls) > MAX_CALLS_SHOWN:
        shown = [*range(14), -1, *range(len(calls) - 12, len(calls))]
    for idx in shown:
        if idx == -1:
            hidden = len(calls) - 26
            lines.append(
                (
                    calls[13]["start_ms"] or 0,
                    f"<div class='gap'>... {hidden} more tool calls not shown ...</div>",
                )
            )
        else:
            lines.append((calls[idx]["start_ms"] or 0, call_html[idx]))
    for t in texts:
        at = t["at_ms"] or 0
        hidden_from = calls[13]["start_ms"] if len(calls) > MAX_CALLS_SHOWN else None
        if hidden_from is not None and hidden_from < at < (calls[-12]["start_ms"] or at):
            continue
        lines.append((at, f"<div class='text'>{e(truncate(t['text'], TEXT_LIMIT))}</div>"))
    lines.sort(key=lambda x: x[0])
    ok = record["pass"]
    verdict = f"<span class='{'pass' if ok else 'fail'}'>{'PASS' if ok else 'FAIL'}</span>"
    cost = record["cost_usd"]
    body = (
        f"<div class='hdr'>fresh claude session &middot; sonnet &middot; {e(setup_label(record['setup']))}"
        f" &middot; {e(record['title'])}</div>"
        f"<div class='prompt'><b>&gt;</b> {e(record['prompt'])}</div>"
        + "".join(h for _, h in lines)
        + "<div class='final'><div class='lbl'>final answer</div>"
        f"<div class='ans'>{e(truncate(record['final_answer'], ANSWER_LIMIT))}</div></div>"
        f"<div class='foot'>{verdict} &middot; {secs(record['wall_s'])} s &middot; "
        f"{record['n_tool_calls']} tool calls &middot; {record['num_turns']} turns &middot; "
        f"{record['tokens']['total']:,} tokens &middot; ${cost:.3f}<br>"
        f"<span class='t'>grader: {e(str(record['grade_reason']))}</span></div>"
    )
    return page(f"terminal {record['task_id']} {record['setup']}", body, TERMINAL_CSS)


# -------------------------------------------------------------------------------- timeline
def _nice_step(span: float) -> float:
    for step in (5, 10, 15, 20, 30, 60, 120):
        if span / step <= 10:
            return float(step)
    return 120.0


def timeline_page(records: list[dict[str, Any]], setup: str, x_max_s: float) -> str:
    """Gantt-style SVG: one lane per task, one bar per tool call, on a shared time axis."""
    recs = [r for r in records if r["setup"] == setup]
    left, right, lane_h, top = 270, 70, 92, 120
    width = 1200
    plot_w = width - left - right
    height = top + lane_h * len(recs) + 80
    scale = plot_w / (x_max_s * 1000)
    step = _nice_step(x_max_s)
    parts = [
        f"<svg xmlns='http://www.w3.org/2000/svg' width='{width}' height='{height}' "
        f"font-family='Helvetica,Arial,sans-serif'><rect width='100%' height='100%' fill='{SURFACE}'/>",
        f"<text x='28' y='44' font-size='26' font-weight='700' fill='{INK}'>"
        f"{e(setup_label(setup))}: what the agent did, over wall-clock time</text>",
        f"<text x='28' y='72' font-size='16' fill='{INK2}'>One row per task, one bar per tool call. "
        f"Gaps between bars are the model thinking. n = 1 run per task, sonnet.</text>",
    ]
    lx = 28
    for kind, label in (
        ("read", "read call"),
        ("write", "write call"),
        ("error", "call that returned an error"),
    ):
        parts.append(
            f"<rect x='{lx}' y='88' width='14' height='14' rx='2' fill='{KIND_COLOR[kind]}'/>"
        )
        parts.append(f"<text x='{lx + 20}' y='100' font-size='15' fill='{INK}'>{label}</text>")
        lx += 40 + 9 * len(label)
    t = 0.0
    while t <= x_max_s + 1e-9:
        x = left + t * 1000 * scale
        parts.append(
            f"<line x1='{x:.1f}' y1='{top - 12}' x2='{x:.1f}' y2='{height - 56}' stroke='{RULE}' stroke-width='1'/>"
        )
        parts.append(
            f"<text x='{x:.1f}' y='{height - 34}' font-size='15' fill='{INK2}' text-anchor='middle'>{t:g}</text>"
        )
        t += step
    parts.append(
        f"<text x='{left + plot_w / 2}' y='{height - 10}' font-size='16' fill='{INK2}' text-anchor='middle'>seconds since the session started</text>"
    )
    for i, r in enumerate(recs):
        y = top + i * lane_h
        mid = y + 44
        verdict = "PASS" if r["pass"] else "FAIL"
        parts.append(
            f"<text x='28' y='{mid - 4}' font-size='18' font-weight='600' fill='{INK}'>{e(r['title'])}</text>"
        )
        parts.append(
            f"<text x='28' y='{mid + 18}' font-size='15' fill='{INK2}'>{verdict} &#183; {r['n_tool_calls']} calls</text>"
        )
        wall = (r["wall_s"] or 0) * 1000
        parts.append(
            f"<rect x='{left}' y='{mid - 2}' width='{wall * scale:.1f}' height='4' fill='{RULE}'/>"
        )
        for c in r["calls"]:
            if c["start_ms"] is None:
                continue
            w = max(4.0, ((c["ms"] or 0) * scale))
            parts.append(
                f"<rect x='{left + c['start_ms'] * scale:.1f}' y='{mid - 12}' width='{w:.1f}' height='24' rx='2' "
                f"fill='{KIND_COLOR[c['kind']]}' stroke='{SURFACE}' stroke-width='1'/>"
            )
        if r["ttfc_s"] is not None:
            fx = left + r["ttfc_s"] * 1000 * scale
            parts.append(
                f"<line x1='{fx:.1f}' y1='{mid - 30}' x2='{fx:.1f}' y2='{mid - 14}' stroke='{INK}' stroke-width='1.5'/>"
            )
            parts.append(
                f"<text x='{fx + 4:.1f}' y='{mid - 33}' font-size='14' fill='{INK}'>first call {r['ttfc_s']:.1f} s</text>"
            )
        ex = left + wall * scale
        parts.append(
            f"<text x='{ex + 8:.1f}' y='{mid + 5}' font-size='15' font-weight='600' fill='{INK}'>{secs(r['wall_s'])} s</text>"
        )
    parts.append("</svg>")
    return page(f"timeline {setup}", "".join(parts), f"body{{background:{SURFACE}}}")


# ------------------------------------------------------------------------------- scoreboard
SCORE_CSS = f"""
body{{background:{SURFACE};color:{INK};font:20px/1.4 Helvetica,Arial,sans-serif;padding:34px 36px}}
h1{{font-size:30px;margin:0 0 6px}}p.sub{{margin:0 0 22px;color:{INK2};font-size:17px}}
table{{border-collapse:collapse;width:100%}}
th{{text-align:left;font-size:15px;color:{INK2};text-transform:uppercase;letter-spacing:.04em;border-bottom:2px solid {INK};padding:8px 8px}}
td{{padding:9px 8px;border-bottom:1px solid {RULE};vertical-align:top}}
td.n,th.n{{text-align:right;font-variant-numeric:tabular-nums}}
tr.tot td{{font-weight:700;border-top:2px solid {INK};border-bottom:none}}
.ok{{color:#0b6b3a;font-weight:700}}.bad{{color:#a4281c;font-weight:700}}
.note{{color:{INK2};font-size:16px}}.setup{{color:{INK2};font-size:16px}}
"""


def score_note(r: dict[str, Any]) -> str:
    if r["status"] != "ok":
        return f"run ended: {r['status']}"
    if not r["pass"]:
        return str(r["grade_reason"])
    bits = []
    if r.get("wrong_write_calls"):
        bits.append(f"{r['wrong_write_calls']} write(s) outside the task")
    extra = sorted(set(r.get("wrong_operations") or []))
    if extra:
        bits.append("reads outside the task: " + ", ".join(extra))
    if r.get("failed_calls"):
        bits.append(f"{r['failed_calls']} call(s) returned an error")
    return "; ".join(bits)


def scoreboard_page(records: list[dict[str, Any]]) -> str:
    rows = []
    for r in records:
        mark = (
            "<span class='ok'>&#10003; pass</span>"
            if r["pass"]
            else "<span class='bad'>&#10007; FAIL</span>"
        )
        rows.append(
            f"<tr><td>{e(r['title'])}</td><td class='setup'>{e(r['setup'])}</td><td>{mark}</td>"
            f"<td class='n'>{secs(r['wall_s'])}</td><td class='n'>{r['n_tool_calls']}</td>"
            f"<td class='n'>{r['tokens']['total']:,}</td><td class='n'>${r['cost_usd']:.3f}</td>"
            f"<td class='note'>{e(score_note(r))}</td></tr>"
        )
    for setup in SETUPS:
        rs = [r for r in records if r["setup"] == setup]
        if not rs:
            continue
        passed = sum(1 for r in rs if r["pass"])
        rows.append(
            f"<tr class='tot'><td>Total</td><td class='setup'>{e(setup)}</td><td>{passed}/{len(rs)} pass</td>"
            f"<td class='n'>{sum(r['wall_s'] or 0 for r in rs):.1f}</td>"
            f"<td class='n'>{sum(r['n_tool_calls'] for r in rs)}</td>"
            f"<td class='n'>{sum(r['tokens']['total'] for r in rs):,}</td>"
            f"<td class='n'>${sum(r['cost_usd'] or 0 for r in rs):.3f}</td><td></td></tr>"
        )
    body = (
        "<h1>Fresh agent, five delegated tasks</h1>"
        "<p class='sub'>n = 1 run per task and setup (sonnet, 10 runs in all). Time in seconds, tokens = input incl. cache + output. "
        "Pass/fail from a deterministic check of the database.</p>"
        "<table><tr><th>Task</th><th>Setup</th><th>Result</th><th class='n'>Time s</th>"
        "<th class='n'>Calls</th><th class='n'>Tokens</th><th class='n'>Cost</th><th>Note</th></tr>"
        + "".join(rows)
        + "</table>"
    )
    return page("scoreboard", body, SCORE_CSS)


# ------------------------------------------------------------------------------- inspector
INSPECT_CSS = f"""
body{{background:{SURFACE};color:{INK};font:20px/1.45 Helvetica,Arial,sans-serif;padding:34px 36px}}
h1{{font-size:28px;margin:0 0 4px}}p.sub{{margin:0 0 22px;color:{INK2};font-size:16px}}
pre{{white-space:pre-wrap;font:19px/1.5 ui-monospace,Menlo,monospace;background:#f1f0ec;border:1px solid {RULE};padding:20px;border-radius:6px;margin:0}}
table{{border-collapse:collapse;width:100%}}
th{{text-align:left;font-size:14px;color:{INK2};text-transform:uppercase;letter-spacing:.04em;border-bottom:2px solid {INK};padding:8px}}
td{{padding:10px 8px;border-bottom:1px solid {RULE};vertical-align:top;font-size:18px}}
code{{font:17px ui-monospace,Menlo,monospace}}
.cols{{columns:3;column-gap:20px;font:15px/1.5 ui-monospace,Menlo,monospace}}
"""


def inspector_header(title: str, data: dict[str, Any]) -> str:
    return (
        f"<h1>{e(title)}</h1><p class='sub'>Returned by a real stdio session (Python MCP client) to "
        f"<code>helpdesk-mcp --variant {e(data['variant'])}</code>; server "
        f"<code>{e(data['server_name'])} {e(data['server_version'])}</code>, protocol "
        f"<code>{e(data['protocol'])}</code>. Nothing below is edited.</p>"
    )


def instructions_page(data: dict[str, Any]) -> str:
    words = len(data["instructions"].split())
    body = (
        inspector_header("Server instructions", data)
        + f"<p class='sub'>{words} words, sent once when the client connects (<code>InitializeResult.instructions</code>).</p>"
        + f"<pre>{e(data['instructions'])}</pre>"
    )
    return page("instructions", body, INSPECT_CSS)


def resources_page(data: dict[str, Any]) -> str:
    rows = "".join(
        f"<tr><td><code>{e(r['uri'])}</code></td><td>{e(r['name'])}</td><td>{e(r['description'])}</td>"
        f"<td><code>{e(r['mime'])}</code></td></tr>"
        for r in [*data["resources"], *data["templates"]]
    )
    body = (
        inspector_header("Resources", data)
        + f"<p class='sub'>resources/list: {len(data['resources'])} &middot; resources/templates/list: {len(data['templates'])}</p>"
        + f"<table><tr><th>URI</th><th>Name</th><th>Description</th><th>MIME type</th></tr>{rows}</table>"
    )
    return page("resources", body, INSPECT_CSS)


def prompts_page(data: dict[str, Any]) -> str:
    rows = "".join(
        f"<tr><td><code>{e(p['name'])}</code></td><td>{e(p['description'])}</td>"
        f"<td>{'<br>'.join('<code>' + e(a) + '</code>' for a in p['arguments']) or '(none)'}</td></tr>"
        for p in data["prompts"]
    )
    body = (
        inspector_header("Prompts", data)
        + f"<p class='sub'>prompts/list: {len(data['prompts'])}</p>"
        + f"<table><tr><th>Name</th><th>Description</th><th>Arguments</th></tr>{rows}</table>"
    )
    return page("prompts", body, INSPECT_CSS)


def tools_page(data: dict[str, Any]) -> str:
    names = "".join(f"<div>{e(n)}</div>" for n in data["tools"])
    body = (
        inspector_header(f"Tools: {len(data['tools'])} listed", data)
        + "<p class='sub'>tools/list names only; each tool also carries a description and an input schema.</p>"
        + f"<div class='cols'>{names}</div>"
    )
    return page("tools", body, INSPECT_CSS)
