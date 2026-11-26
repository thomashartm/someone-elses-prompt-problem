#!/usr/bin/env python3
"""Emit the operator-facing capability sheet (single source of truth).

Everything printed is DERIVED from the repo, not restated: the potency ladder
from AGENTS.md, the profile ceilings + potency binding from garak_battery,
the profile probe sets from configs/garak_profiles.json, slash commands from
the .pi/extensions sources, and make targets from the Makefile. If a fact is
not derivable, the section says so instead of guessing.

Usage:
    capabilities.py                 # human-readable markdown
    capabilities.py --json          # machine-readable (for tests/tools)

Exit 0 always (this is a read-only viewer); missing pieces degrade sections.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
AGENTS = ROOT / "AGENTS.md"
CONFIG = ROOT / "configs" / "garak_profiles.json"
MAKEFILE = ROOT / "Makefile"
EXTENSIONS = ROOT / ".pi" / "extensions"

sys.path.insert(0, str(HERE))

import garak_battery  # noqa: E402  (offline module; exposes CEILINGS + POTENCY_PROFILES)


def potency_binding() -> dict:
    import garak_battery
    return {lvl: sorted(ps) for lvl, ps in sorted(garak_battery.POTENCY_PROFILES.items())}


def potency_ladder_rows() -> list[list[str]]:
    """Parse the ladder table from AGENTS.md (rows like | `stealth` | 5 | 3× | ...)."""
    rows = []
    for line in AGENTS.read_text().splitlines():
        m = re.match(r"^\|\s*`(\w+)`\s*\|\s*(.+?)\s*\|\s*(.+?)\s*\|\s*(.+?)\s*\|$", line)
        if m and m.group(1) in ("stealth", "standard", "aggressive", "full"):
            rows.append([m.group(1), m.group(2), m.group(3), m.group(4)])
    return rows


def battery_profiles() -> dict:
    import garak_battery
    cfg = json.loads(CONFIG.read_text())["profiles"]
    return {
        name: {
            "description": spec.get("description", ""),
            "predicted_requests": spec.get("predicted_requests"),
            "ceiling": garak_battery.CEILINGS.get(name),
            "generations": spec.get("generations", 1),
            "probes": sorted(spec.get("probes", {})),
        }
        for name, spec in cfg.items()
    }


def _balanced_span(text: str, start: int) -> str:
    """Source from `start` (right after registerCommand's '(') through the
    token balancing the argument list - bracket- and string-aware."""
    depth = 0
    i = start
    quote = None
    while i < len(text):
        ch = text[i]
        if quote:
            if ch == "\\":
                i += 2
                continue
            if ch == quote:
                quote = None
        elif ch in "\"'`":
            quote = ch
        elif ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
            if depth == 0:
                return text[start:i]
        i += 1
    return text[start:start + 2000]


def slash_commands() -> dict[str, str]:
    """Unique registerCommand names across extensions; handler identifier
    resolved via its const block, inline/spread descriptions taken literally."""
    blocks: dict[str, str] = {}
    cmds: dict[str, str] = {}
    for ts in sorted(EXTENSIONS.glob("*.ts")):
        text = ts.read_text()
        for m in re.finditer(r"const\s+(\w+)\s*=\s*\{", text):
            window = text[m.end():m.end() + 400]
            d = re.search(r'description:\s*[`"]([^`"]{0,110})', window)
            if d:
                blocks[m.group(1)] = d.group(1)
        for m in re.finditer(r'registerCommand\(\s*"([a-z0-9-]+)"\s*,', text):
            name = m.group(1)
            expr = _balanced_span(text, m.end()).strip()
            ident = re.match(r"[\w$]+", expr)
            desc = blocks.get(ident.group(0), "") if ident else ""
            if not desc:
                d = re.search(r'description:\s*[`"]([^`"]{0,110})', expr)
                desc = d.group(1) if d else ""
            # extensions may re-register a name as an alias delegate; the
            # first real (non-"Alias of") description wins, alias text fills gaps
            prev = cmds.get(name)
            if prev is None or (prev.startswith("Alias of") and desc
                                and not desc.startswith("Alias of")):
                cmds[name] = desc
    return cmds


def make_targets() -> list[str]:
    names = []
    for line in MAKEFILE.read_text().splitlines():
        m = re.match(r"^([a-z][a-z0-9-]*):", line)
        if m and not line.startswith("\t"):
            names.append(m.group(1))
    return sorted(set(names))


CMD_HINTS = {
    "workspace": "one workspace per hunt: ensure --host --url [--hunt NAME] [--potency LVL]",
    "finish": "mark a workspace closed (cleanup prerequisite)",
    "prune": "cleanup report/deletion: --days N [--finished] [--id SID] [--host H] [--apply]",
}


def _cell(desc: str) -> str:
    """Table-safe, single-line cell."""
    return re.sub(r"\s*\|+\s*", " · ", desc.strip()) or "*no description registered*"


def markdown() -> str:
    lines = ["# SEPP capabilities", ""]

    lines += ["## Potency ladder (what a run may spend)", "",
              "| Level | Burst max | Pause floor | Probe classes | Battery profiles |",
              "|---|---|---|---|---|"]
    bind = potency_binding()
    for row in potency_ladder_rows():
        lvl = row[0]
        prof = ", ".join(bind[lvl]) if lvl in bind else "?"
        lines.append("| `%s` | %s | %s | %s | %s |" % (*row, prof))
    if not potency_ladder_rows():
        lines.append("(ladder table not found in AGENTS.md - parse failed)")
    lines.append("")

    lines += ["## Garak battery profiles (`garak_battery.py --profile ...`)", ""]
    for name, spec in battery_profiles().items():
        lines.append("**%s** - %s" % (name, spec["description"]))
        lines.append("- predicted %s requests (ceiling %s, generations %s)" %
                     (spec["predicted_requests"], spec["ceiling"], spec["generations"]))
        lines.append("- probes (%d): `%s`" % (len(spec["probes"]), "`, `".join(spec["probes"])))
        lines.append("")
    lines += ["Other runner switches: `--slug`, `--environment`, `--conv-mode fresh|reuse`,",
              "`--port`, `--dry-run`, `--report-out`, `--potency` (bond from the ladder).", ""]

    cmds = slash_commands()
    lines += ["## Agent-shell commands (registered in .pi/extensions)", ""]
    if cmds:
        lines += ["| Command | Purpose |", "|---|---|"]
        for name, desc in cmds.items():
            lines.append("| `/%s` | %s |" % (name, _cell(desc)))
    else:
        lines.append("(no registerCommand definitions found)")
    lines += ["", "Aliases: every `/sepp-*` spelling mirrors its `/hunt-*` twin.", ""]

    lines += ["## Make targets", "", "`%s`" % "` `".join(make_targets()), ""]

    lines += ["## Hunt workspace lifecycle", ""]
    for k, v in CMD_HINTS.items():
        lines.append("- `workspace.py %s` - %s" % (k, v))
    lines += ["", "Full contracts: `AGENTS.md` (control plane), `README.md` (operator steering),",
              "`scripts/README.md` (tool index)."]
    return "\n".join(lines) + "\n"


def json_doc() -> dict:
    return {
        "potency_ladder": potency_ladder_rows(),
        "potency_battery_binding": potency_binding(),
        "battery_profiles": battery_profiles(),
        "slash_commands": slash_commands(),
        "make_targets": make_targets(),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    args = ap.parse_args()
    print(json.dumps(json_doc(), indent=2) if args.json else markdown())
    return 0


if __name__ == "__main__":
    sys.exit(main())
