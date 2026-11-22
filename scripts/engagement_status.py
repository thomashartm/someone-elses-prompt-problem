#!/usr/bin/env python3
"""engagement_status.py — assemble the controller's leg-closing brief from disk.

Sources (nothing invented; every number traced):
  data/hunters/*.json                — board: legs, phases, stop states, results
  data/reports/findings.json         — the ledger (severities, statuses, titles)
  data/workspaces/*/                 — targets (session.json) + latest report tails
  reports "next probes" convention   — lines like "- 1. ..." / "... (needs approval)"

Output: markdown brief on stdout shaped like the controller's chat summary:
  Where the hunt stands / Attack-surface picture / Honest conclusion /
  Decisions needed / Next steps.

Usage:
  python3 scripts/engagement_status.py [workspace-id-substring]   # default: most recently updated workspace
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HUNTERS = ROOT / "data" / "hunters"
LEDGER = ROOT / "data" / "reports" / "findings.json"
WORKSPACES = ROOT / "data" / "workspaces"
SEV_NAME = {0: "crit", 1: "high", 2: "med", 3: "low"}
SEV_BADGE = {0: "🔥", 1: "🟠", 2: "🟡", 3: "⚪"}
ICON = {"active": "●", "quiet": "○", "done": "✓"}


def load_ledger(target_substr: str) -> list[dict]:
    if not LEDGER.exists():
        return []
    rows = json.loads(LEDGER.read_text())
    out = []
    for r in rows:
        # associate loosely: request id appears in any workspace of this engagement,
        # or the finding was recorded today for this host
        out.append(r)
    return out


def pick_workspace(substr: str | None) -> Path | None:
    if not WORKSPACES.exists():
        return None
    dirs = [d for d in WORKSPACES.iterdir() if d.is_dir()]
    if substr:
        dirs = [d for d in dirs if substr in d.name]
    if not dirs:
        return None
    return max(dirs, key=lambda d: (d / "session.json").stat().st_mtime if (d / "session.json").exists() else 0)


def load_board() -> list[tuple[dict, float]]:
    rows = []
    if HUNTERS.exists():
        for p in sorted(HUNTERS.glob("*.json")):
            try:
                r = json.loads(p.read_text())
                age = time.time() - r.get("updatedAtEpoch", 0)
                r["state"] = "done" if r.get("done") else ("quiet" if age > 600 else "active")
                rows.append((r, age))
            except Exception:
                continue
    return rows


def findings_for_targets(ws: Path | None) -> list[dict]:
    if not LEDGER.exists() or ws is None:
        return []
    # associate: any Caido request id (4-5 digits) cited in the workspace's
    # reports OR evidence filenames, PLUS same-day ledger rows as fallback
    req_ids: set[str] = set()
    for f in list((ws / "reports").glob("*.md")) + list((ws / "evidence").glob("*")):
        if f.is_dir():
            continue
        for m in re.findall(r"\b(2\d{3,4})\b", f.read_text(errors="replace")[:200_000] if f.suffix == ".md" else f.name):
            req_ids.add(m)
    try:
        rows = json.loads(LEDGER.read_text())
    except Exception:
        return []
    out = [r for r in rows if str(r.get("request_id", "")) in req_ids]
    if not out:  # cold fallback: today's findings
        today = time.strftime("%Y-%m-%d")
        out = [r for r in rows if r.get("created_at", "").startswith(today)]
    seen, uniq = set(), []
    for r in out:
        if r["id"] not in seen:
            seen.add(r["id"])
            uniq.append(r)
    return uniq


def parse_report(ws: Path) -> dict:
    """Extract the narrative pieces: head, statuses, proposed next probes."""
    rep_dir = ws / "reports"
    if not rep_dir.exists():
        return {}
    reps = sorted(rep_dir.glob("*.md"))
    if not reps:
        return {}
    text = reps[-1].read_text(errors="replace")
    statuses = {s: len(re.findall(rf"\bStatus:\s*{s}\b", text, re.I)) for s in ("proven", "probable", "hunch")}
    # proposed probes: ONLY from explicit proposal lists ("- 1. ..." style near
    # "propose/next probe/Needs your" headings), split by approval gating
    probes: list[str] = []
    in_block = False
    for line in text.splitlines():
        l = line.strip()
        if re.search(r"#{2,4}.*(propos|next step)|proposed next probes", l, re.I):
            in_block = True
            continue
        if l.startswith("#"):
            in_block = False
            continue
        if in_block and re.match(r"^(?:[-*]\s*)?(?:\d+[.)]\s*)", l):
            item = re.sub(r"^(?:[-*]\s*)?(?:\d+[.)]\s*)", "", l).strip()
            if len(item) > 12:
                probes.append(item)
    if not probes:  # fallback anywhere in the report
        probes = [re.sub(r"^(?:[-*]\s*)?(?:\d+[.)]\s*)", "", l).strip()
                  for l in text.splitlines()
                  if re.match(r"^\s*(?:[-*]\s*)?\d+[.)]\s+", l)
                  and re.search(r"approval|operator|principal|read-only", l, re.I)]
    return {"file": reps[-1].name, "tail": text[-1500:], "statuses": statuses, "probes": probes}


def board_lines(board: list[tuple[dict, float]]) -> list[str]:
    lines = []
    for r, _age in board:
        state = r.get("state", "?")
        lines.append(
            f"| {ICON.get(state, '!')} `{r.get('name','?')}` | {state} | {r.get('phase','?')} | "
            f"{(r.get('result') or r.get('activity') or '')[:80]} |"
        )
    return lines


def main() -> int:
    substrate = sys.argv[1] if len(sys.argv) > 1 else None
    ws = pick_workspace(substrate)
    board = load_board()
    findings = findings_for_targets(ws)
    rep = parse_report(ws) if ws else {}

    targets = "?"
    ws_updated = "?"
    if ws:
        try:
            sess = json.loads((ws / "session.json").read_text())
            ts = sess.get("targets", [])
            targets = ", ".join(t["url"] if isinstance(t, dict) else str(t) for t in ts) or "?"
        except Exception:
            pass
        ws_updated = time.strftime("%Y-%m-%d %H:%M", time.localtime((ws / "session.json").stat().st_mtime))

    # ---- where the hunt stands
    live = [r for r, _ in board if r.get("state") != "done"]
    done = [(r, a) for r, a in board if r.get("state") == "done"]
    stand = ["## Where the hunt stands", "",
             f"- Engagement: **{targets}** — workspace `{ws.name if ws else '?'}` (last activity {ws_updated})",
             f"- Legs closed: **{len(done)}** · hunters live right now: **{len(live)}**"
             + (f" ({', '.join(r.get('name','?') for r in live)})" if live else ""),
             f"- Findings on the ledger for this engagement: **{len(findings)}**"]
    if live:
        stand.append("- ⚠ legs still running — figures below are a snapshot, not a close-out.")
    md = "\n".join(stand)

    # ---- attack surface picture (grouped by severity, honest statuses)
    md += "\n\n## Attack-surface picture\n\n| Sev | Finding (by the ledger) | Status | Req |\n|---|---|---|---|\n"
    order = sorted(findings, key=lambda r: (r.get("severity", 9), r.get("created_at", "")))
    for f in order:
        sev = SEV_NAME.get(f.get("severity"), "?")
        md += (f"| {SEV_BADGE.get(f.get('severity'),'?')} {sev} | {f.get('title','?')[:100]} "
               f"| {f.get('status','?')} | {f.get('request_id','?')} |\n")
    if not findings:
        md += "| – | (no findings recorded for this engagement yet) | – | – |\n"

    # ---- honest conclusion
    st = rep.get("statuses") or {}
    counts = {s: sum(1 for f in findings if f.get("status") == s) for s in ("proven", "probable", "hunch")}
    positives = sum(1 for f in findings if re.search(r"isolation|holds|enforc|by-design", f.get("title", ""), re.I))
    holes = sum(1 for f in findings if f.get("status") == "hunch")
    concl = ["## Honest conclusion", ""]
    if counts["proven"]:
        concl.append(f"- {counts['proven']} proven finding(s); evidence chain = request pairs in the ledger")
    if positives:
        concl.append(f"- {positives} of them are POSITIVE controls (isolation/enforcement holds) — the perimeter narrows; do not inflate them into findings they are not")
    if counts["hunch"] or holes:
        concl.append(f"- {holes} hunch(es) on the ledger — hypotheses only, need dedicated probes before anyone acts on them")
    if not counts["proven"] and not counts["hunch"]:
        concl.append("- Nothing proven yet — surface mapped, verdicts pending")
    md += "\n".join(concl)

    # ---- decisions needed + next steps (from report conventions)
    md += "\n\n## Decisions needed\n\n"
    probes = rep.get("probes") or []
    approval = [p for p in probes if re.search(r"state-chang|needs? (operator|approval)|second principal|write route", p, re.I)]
    free = [p for p in probes if p not in approval]
    if approval:
        for i, p in enumerate(approval[:3], 1):
            md += f"- {i}. {p[:160]}\n"
    else:
        md += "- (none pending — latest report raises no operator-gated items)\n"

    md += "\n## Next steps\n\n"
    for i, p in enumerate(free[:3], 1):
        md += f"- {i}. {p[:160]}\n"
    md += f"- Full report: `data/workspaces/{ws.name if ws else '?'}/reports/{rep.get('file','?')}`\n"
    md += f"- Board: `python3 scripts/hunter_board.py list` · Ledger: `python3 helpers/findings.py list`\n"

    print(md)
    return 0


if __name__ == "__main__":
    sys.exit(main())
