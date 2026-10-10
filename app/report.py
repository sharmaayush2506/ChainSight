"""Investigator report as PDF (fund-flow graph, risk, nearest VASP, actions, evidence)."""
from __future__ import annotations

import io
from datetime import datetime, timezone

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import networkx as nx  # noqa: E402
from reportlab.lib import colors  # noqa: E402
from reportlab.lib.pagesizes import A4  # noqa: E402
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet  # noqa: E402
from reportlab.lib.units import mm  # noqa: E402
from reportlab.platypus import (Image, PageBreak, Paragraph, Preformatted,  # noqa: E402
                                SimpleDocTemplate, Spacer, Table, TableStyle)

TYPE_COLORS = {"seed": "#d62728", "source": "#9467bd", "intermediary": "#7f7f7f",
               "deposit": "#ff7f0e", "exchange": "#2ca02c", "mixer": "#111111",
               "bridge": "#17becf"}


def short(a: str) -> str:
    return a if len(a) <= 14 else f"{a[:6]}..{a[-4:]}"


def _safe(text: str) -> str:
    """The built-in PDF fonts lack a few symbols; keep the text readable."""
    for a, b in {"≈": "~", "→": "->", "…": "...", "✔": "[x]", "₹": "INR "}.items():
        text = text.replace(a, b)
    return text


def _p(text: str, style) -> Paragraph:
    return Paragraph(_safe(text), style)


def _ts(ts: int | None) -> str:
    if not ts:
        return "-"
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%d %b %Y %H:%M UTC")


def graph_png(case: dict) -> bytes:
    g = nx.DiGraph()
    for n in case["graph"]["nodes"]:
        g.add_node(n["id"], **n)
    for e in case["graph"]["edges"]:
        g.add_edge(e["source"], e["target"], amount=e["amount"])
    by_hop: dict[int, list[str]] = {}
    for n, d in g.nodes(data=True):
        by_hop.setdefault(d.get("hop") if d.get("hop") is not None else 0, []).append(n)
    pos = {n: (hop, -(i - (len(ns) - 1) / 2)) for hop, ns in by_hop.items()
           for i, n in enumerate(ns)}
    fig, ax = plt.subplots(figsize=(11, 6), dpi=110)
    colors_ = [TYPE_COLORS.get(d.get("type"), "#7f7f7f") for _, d in g.nodes(data=True)]
    widths = [0.6 + 3 * (d["amount"] / max(1, max(x["amount"] for x in case["graph"]["edges"])))
              for _, _, d in g.edges(data=True)]
    nx.draw_networkx_edges(g, pos, ax=ax, width=widths, alpha=0.5, arrows=True, arrowsize=10,
                           connectionstyle="arc3,rad=0.05")
    nx.draw_networkx_nodes(g, pos, ax=ax, node_color=colors_, node_size=260)
    names = {"seed": "Reported", "deposit": "Deposit", "exchange": "Exchange", "bridge": "Bridge",
             "mixer": "Mixer"}
    labels = {n: f"{names[d['type']]}\n{(d.get('entity') or '').upper()}"
              if d.get("type") in names else "" for n, d in g.nodes(data=True)}
    label_pos = {n: (x, y - 0.28) for n, (x, y) in pos.items()}
    nx.draw_networkx_labels(g, label_pos, labels, font_size=7, ax=ax)
    for t, c in TYPE_COLORS.items():
        ax.scatter([], [], c=c, label=t)
    ax.legend(loc="lower right", fontsize=7, frameon=False)
    ax.set_axis_off()
    buf = io.BytesIO()
    fig.tight_layout()
    fig.savefig(buf, format="png")
    plt.close(fig)
    return buf.getvalue()


def build_pdf(case: dict) -> bytes:
    styles = getSampleStyleSheet()
    small = ParagraphStyle("small", parent=styles["BodyText"], fontSize=8, leading=10)
    mono = ParagraphStyle("mono", parent=styles["Code"], fontSize=7, leading=9)
    story = []
    risk = case["risk"]
    story += [
        _p("ChainSight - Investigator Report", styles["Title"]),
        _p(f"Case {case['id']} - complaint ref {case.get('complaint_ref') or '-'} - "
                  f"generated {_ts(int(datetime.now(timezone.utc).timestamp()))}", small),
    ]
    if case.get("demo") or case.get("tinyfish_mode") == "simulated":
        story.append(_p("<font color='red'><b>DEMO DATA</b> - synthetic on-chain data "
                               "and/or simulated TinyFish replies.</font>", small))
    story.append(Spacer(1, 4 * mm))

    v = (case.get("vasps") or [None])[0]
    summary = [
        ["Reported wallet", f"{case['address']} ({case['chain'].title()})"],
        ["Risk (lead score)", f"{risk['level']} - {risk['score']}/100"],
        ["Likely category", risk["category"]],
        ["Nearest VASP", f"{v['name']} - {v['hops']} hop(s), ~{v['amount_estimate']:,.0f} "
                         f"{v['token']}" if v else "None found within hop limit"],
        ["Address at VASP", v["address"] if v else "-"],
        ["Time to report", f"{case['timings'].get('total', 0):.1f} s (measured)"],
    ]
    t = Table(summary, colWidths=[40 * mm, 130 * mm])
    t.setStyle(TableStyle([("FONTSIZE", (0, 0), (-1, -1), 9),
                           ("BACKGROUND", (0, 0), (0, -1), colors.whitesmoke),
                           ("GRID", (0, 0), (-1, -1), 0.3, colors.grey)]))
    story += [t, Spacer(1, 3 * mm),
              _p(f"<i>{risk['disclaimer']}</i>", small), Spacer(1, 3 * mm)]

    story.append(_p("Fund flow", styles["Heading2"]))
    story.append(Image(io.BytesIO(graph_png(case)), width=180 * mm, height=98 * mm))

    story.append(_p("Suggested next actions", styles["Heading2"]))
    for a in case.get("next_actions", []):
        story.append(_p(_safe(f"- {a}"), small))

    story.append(_p("Why this score", styles["Heading2"]))
    for r in risk["reasons"]:
        story.append(_p(f"+{r['points']}  {r['reason']}", small))

    story.append(_p("Laundering patterns", styles["Heading2"]))
    for p in case.get("patterns", []):
        story.append(_p(f"<b>{p['type'].replace('_', ' ')}</b> - {p['detail']} "
                               f"({', '.join(short(a) for a in p['addresses'][:5])})", small))

    story.append(_p("VASPs reached and law-enforcement contacts", styles["Heading2"]))
    for vv in case.get("vasps", []):
        c = case.get("contacts", {}).get(vv["entity"], {})
        story.append(_p(
            f"<b>{vv['name']}</b> - {vv['address_type']} {vv['address']} - {vv['hops']} hops - "
            f"arrived {_ts(vv['arrived_ts'])}", small))
        if c:
            story.append(_p(f"Portal: {c.get('portal_url') or '-'} - Email: "
                                   f"{c.get('contact_email') or '-'}"
                                   f"{' - <b>MANUAL REVIEW</b>' if c.get('manual_review') else ''}",
                                   small))
            for s in c.get("process_steps") or []:
                story.append(_p(f"&nbsp;&nbsp;- {s}", small))

    intel = case.get("intel", {})
    story.append(_p("Off-chain intelligence", styles["Heading2"]))
    for m in intel.get("mentions", []):
        story.append(_p(f"{'[confirmed]' if m['confirmed'] else '[unconfirmed]'} <b>{m.get('title') or m['url']}</b>"
                               f" - {m['url']}", small))
    if not intel.get("mentions"):
        story.append(_p("No public mentions found.", small))
    story.append(_p(f"Sanctions lists: {intel.get('sanctions') or 'no match'}", small))

    story.append(PageBreak())
    story.append(_p("Evidence trail", styles["Heading2"]))
    rows = [["#", "Captured (UTC)", "URL / purpose", "SHA-256"]]
    for e in case.get("evidence", []):
        rows.append([str(e["id"]), e["captured_at"],
                     _p(f"{e['url']}<br/>{e['purpose']} - {e['method']}", small),
                     _p(e["sha256"], mono)])
    t = Table(rows, colWidths=[8 * mm, 34 * mm, 80 * mm, 52 * mm], repeatRows=1)
    t.setStyle(TableStyle([("FONTSIZE", (0, 0), (-1, -1), 7),
                           ("GRID", (0, 0), (-1, -1), 0.3, colors.grey),
                           ("VALIGN", (0, 0), (-1, -1), "TOP")]))
    story.append(t)

    story.append(_p("Transactions on the path to the nearest VASP", styles["Heading2"]))
    if v:
        hops = set(zip(v["path"], v["path"][1:]))
        for e in case["graph"]["edges"]:
            if (e["source"], e["target"]) in hops:
                story.append(_p(
                    f"{short(e['source'])} -> {short(e['target'])}: {e['amount']:,.2f} {e['token']} "
                    f"at {_ts(e['first_ts'])}", small))
                for h in e["tx_hashes"]:
                    story.append(_p(h, mono))

    if case.get("freeze_request"):
        story.append(PageBreak())
        story.append(_p("Draft freeze / disclosure request", styles["Heading2"]))
        story.append(Preformatted(case["freeze_request"], mono))

    buf = io.BytesIO()
    SimpleDocTemplate(buf, pagesize=A4, leftMargin=15 * mm, rightMargin=15 * mm,
                      topMargin=15 * mm, bottomMargin=15 * mm,
                      title=f"ChainSight report {case['id']}").build(story)
    return buf.getvalue()
