#!/usr/bin/env python3
"""hunter_board.py — live roster of SEPP hunters (the hunter heartbeat bus).

Every hunter, however it was launched (pi controller, OpenCode session, a cron
wrapper — anything), registers itself and keeps its row fresh:

    python3 scripts/hunter_board.py heartbeat \
        --name hunt-1 --target https://testbed.internal \
        --workspace data/workspaces/20261122-testbed.internal \
        --phase map --activity "probing /api /v1 routes"

Call heartbeat again on every milestone (start, phase flip, after each probe
batch, when waiting on approval). Final call adds --done "one-line result".
Rows live in data/hunters/<name>.json. Readers:
    list    — machine table (markdown-ish) of all rows, staleness-aware
    get NAME — single row as JSON

A row is QUIET when updated >10 min ago and not done (watchdog hint), DONE
until pruned (kept 24h for the record).
"""
import argparse
import json
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BOARD = REPO / "data" / "hunters"
QUIET_AFTER_S = 600      # 10 min without heartbeat → QUIET
DONE_KEEP_S = 86400      # prune done rows after 24 h


def _atomic(path: Path, data: dict) -> None:
    tmp = path.with_suffix(f".{time.time_ns()}.tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n")
    tmp.replace(path)


def cmd_heartbeat(a: argparse.Namespace) -> int:
    BOARD.mkdir(parents=True, exist_ok=True)
    path = BOARD / f"{a.name}.json"
    prev = {}
    try:
        prev = json.loads(path.read_text())
    except Exception:
        pass
    row = {
        "name": a.name,
        "target": a.target or prev.get("target", ""),
        "workspace": str(a.workspace or prev.get("workspace", "")),
        "phase": a.phase or prev.get("phase", "orient"),
        "activity": a.activity or prev.get("activity", ""),
        "pid": a.pid or prev.get("pid"),
        "startedAt": prev.get("startedAt") or time.strftime("%Y-%m-%dT%H:%M:%S"),
        "updatedAt": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "updatedAtEpoch": int(time.time()),
        "done": a.done is not None,
        "result": a.done if a.done is not None else prev.get("result", ""),
        "source": a.source or prev.get("source", "unknown"),
        # cooperative-stop flag: hunters check their own row before each probe
        # batch; set by `stop` subcommand (survives heartbeat refreshes)
        "stopRequested": prev.get("stopRequested", False),
    }
    _atomic(path, row)
    state = "done" if row["done"] else "active"
    print(f"{row['name']}: {state} — {row['phase']} — {row['activity']}")
    return 0


def _decorate(row: dict, now: int) -> dict:
    age = now - row.get("updatedAtEpoch", 0)
    if row.get("done"):
        row["state"] = "done"
    elif row.get("stopRequested"):
        row["state"] = "stopping"
    elif age > QUIET_AFTER_S:
        row["state"] = "quiet"
    else:
        row["state"] = "active"
    row["ageMin"] = round(age / 60, 1)
    return row


def cmd_get(a: argparse.Namespace) -> int:
    path = BOARD / f"{a.name}.json"
    if not path.exists():
        print(json.dumps({"error": f"no hunter named {a.name}"}))
        return 1
    row = _decorate(json.loads(path.read_text()), int(time.time()))
    print(json.dumps(row, indent=2))
    return 0


def _pid_alive(pid: int) -> bool:
    import os
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _pid_looks_like_ours(pid: int, row: dict) -> tuple[bool, str]:
    """Guard against PID reuse: only kill when ps output plausibly matches."""
    import subprocess
    try:
        out = subprocess.run(
            ["ps", "-p", str(pid), "-o", "command="],
            capture_output=True, text=True, timeout=5,
        ).stdout.strip()
    except Exception as e:
        return False, f"ps failed: {e}"
    if not out:
        return False, "process gone"
    hay = f"{row.get('name', '')} {row.get('workspace', '')}".lower()
    cmd = out.lower()
    # accept: mentions the row/workspace, or is python3 (typical hunter shell)
    if any(tok and tok in cmd for tok in hay.split("/")) or cmd.startswith(("python", "sh ", "bash ", "/bin/")):
        return True, out[:80]
    return False, f"unrelated-looking cmdline: {out[:80]}"


def cmd_stop(a: argparse.Namespace) -> int:
    """Cooperative stop: flag the row; the hunter wraps up at its next row check.

    Subagent hunters CANNOT be SIGTERM-killed reliably (their bash-tool shells
    are throwaway, so recorded PIDs die instantly) — requesting a stop and
    letting the agent wind down is the honest mechanism for them.
    """
    path = BOARD / f"{a.name}.json"
    if not path.exists():
        print(f"no hunter named {a.name}")
        return 1
    try:
        row = json.loads(path.read_text())
    except Exception as e:
        print(f"corrupt row: {e}")
        return 1
    if row.get("done"):
        print(f"✓ {a.name}: already done — nothing to stop")
        return 0
    row["stopRequested"] = True
    row["updatedAt"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    row["updatedAtEpoch"] = int(time.time())
    _atomic(path, row)
    print(f"⏹ {a.name}: STOP REQUESTED — hunter winds down at its next row check "
          f"(tracker: {row.get('activity', '')[:60]})")
    return 0


def cmd_abort(a: argparse.Namespace) -> int:
    import signal, time as _t
    now = int(_t.time())
    BOARD.mkdir(parents=True, exist_ok=True)
    targets = []
    for p in sorted(BOARD.glob("*.json")):
        try:
            row = json.loads(p.read_text())
        except Exception:
            continue
        if a.name and row.get("name") != a.name:
            continue
        if not row.get("done"):
            targets.append((p, row))
    if not targets:
        print("(nothing to abort — no live rows)" + ("" if not a.name else f" for {a.name}"))
        return 0
    import os
    for p, row in targets:
        pid = row.get("pid")
        fate = "no pid recorded"
        age = now - row.get("updatedAtEpoch", 0)
        if isinstance(pid, int) and pid != os.getpid():
            if not _pid_alive(pid):
                fate = "already exited"
            elif age <= QUIET_AFTER_S:
                # fresh heartbeat: the owner just registered this PID — trust it
                sig = signal.SIGKILL if a.kill else signal.SIGTERM
                try:
                    os.kill(pid, sig)
                    fate = f"SIG{'KILL' if a.kill else 'TERM'} → {pid} (fresh row)"
                    if not a.kill:
                        _t.sleep(2.0)
                        if _pid_alive(pid):
                            os.kill(pid, signal.SIGKILL)
                            fate += " → escalated SIGKILL"
                except OSError as e:
                    fate = f"kill failed: {e}"
            else:
                ok, why = _pid_looks_like_ours(pid, row)
                if ok:
                    sig = signal.SIGKILL if a.kill else signal.SIGTERM
                    try:
                        os.kill(pid, sig)
                        fate = f"signal {sig.name} sent to {pid} ({why})"
                        if not a.kill:
                            _t.sleep(2.0)  # grace, then escalate
                            if _pid_alive(pid):
                                os.kill(pid, signal.SIGKILL)
                                fate += " → escalated SIGKILL"
                    except OSError as e:
                        fate = f"kill failed: {e}"
                else:
                    fate = f"SKIPPED (stale row, guard: {why}) — use --kill to override"
        row["done"] = True
        row["stopRequested"] = True  # belt+braces: in-process agents notice even if the kill missed them
        row["result"] = f"ABORTED by operator @{time.strftime('%H:%M:%S')} — {fate}"
        row["updatedAt"], row["updatedAtEpoch"] = row["result"], now
        _atomic(p, row)
        print(f"✗ {row['name']}: {row['result']}")
    return 0


def cmd_list(_a: argparse.Namespace) -> int:
    now = int(time.time())
    rows = []
    for p in sorted(BOARD.glob("*.json")):
        try:
            rows.append(_decorate(json.loads(p.read_text()), now))
        except Exception as e:
            rows.append({"name": p.stem, "state": "corrupt", "activity": str(e)})
    # prune ancient done rows on read (cheap opportunistic GC)
    for p in list(BOARD.glob("*.json")):
        try:
            r = json.loads(p.read_text())
            if r.get("done") and now - r.get("updatedAtEpoch", 0) > DONE_KEEP_S:
                p.unlink()
        except Exception:
            pass
    order = {"active": 0, "quiet": 1, "done": 2, "corrupt": 3}
    rows.sort(key=lambda r: (order.get(r.get("state", "corrupt"), 9), -r.get("updatedAtEpoch", 0)))
    for r in rows:
        marker = {"active": "●", "stopping": "⏹", "quiet": "○", "done": "✓"}.get(r["state"], "!")
        print(f"{marker} {r['name']:<12} [{r['state']:^5}] {r.get('target','?'):<42} "
              f"{r.get('phase','?'):<11} {r.get('activity','')[:60]} ({r.get('ageMin','?')}m ago)")
    if not rows:
        print("(no hunters have heartbeated yet)")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    hb = sub.add_parser("heartbeat", help="register/update a hunter row")
    hb.add_argument("--name", required=True, help="unique hunter name, e.g. hunt-1")
    hb.add_argument("--target", help="hunt target URL")
    hb.add_argument("--workspace", help="session workspace path (absolute or repo-relative)")
    hb.add_argument("--phase", choices=["orient", "map", "hypothesize", "prove", "record", "report", "wait-operator"])
    hb.add_argument("--activity", help="one line: what the hunter is doing RIGHT NOW")
    hb.add_argument("--done", metavar="RESULT", help="finish the row with a one-line result")
    hb.add_argument("--pid", type=int, help="hunter process id — MANDATORY for real legs, enables /sepp-abort")
    hb.add_argument("--source", help='"pi:hunt-<id>" | "opencode:<session>" | tool name')

    st = sub.add_parser("stop", help="cooperative stop: flag row; hunter winds down at next check (subagent-safe)")
    st.add_argument("name", help="hunter to stop")

    ab = sub.add_parser("abort", help="terminate hunter process(es) and mark rows aborted")
    ab.add_argument("name", nargs="?", help="one hunter only; default: ALL active/quiet rows")
    ab.add_argument("--kill", action="store_true", help="escalate straight to SIGKILL")

    getp = sub.add_parser("get", help="one row as JSON (detail view source)")
    getp.add_argument("name")

    lst = sub.add_parser("list", help="the board: all hunters, staleness-aware")
    lst.add_argument("--json", action="store_true", dest="as_json")

    a = ap.parse_args()
    if a.cmd == "heartbeat":
        return cmd_heartbeat(a)
    if a.cmd == "get":
        return cmd_get(a)
    if a.cmd == "stop":
        return cmd_stop(a)
    if a.cmd == "abort":
        return cmd_abort(a)
    return cmd_list(a)


if __name__ == "__main__":
    sys.exit(main())
