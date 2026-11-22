# Backlog

Feature and improvement tracker for the hunting harness. Prioritized top-down.
Status: `open` / `in-progress` / `done (YYYY-MM-DD)` / `blocked (why)`.

---

## 1. Target panel: component-factory widget upgrade — DONE

> Originally the panel used plain string lines (no colors, no box). Operator chose
> the component-factory approach for a themed visual panel.

Implemented 2026-11-20 in `.pi/extensions/sepp.ts`:

- Factory returns a `Box(1,0)` from `@earendil-works/pi-tui` with themed `Text`s:
  accent `⌖ target` label + bold URL; muted `↳ recent` line for the latest
  observed endpoint (hidden when identical).
- Placement `belowEditor`, key `sepp-target`; pi disposes the previous component
  on replacement (no leak).
- Lesson: pi 1.5.2 does NOT re-export tui classes from `pi-coding-agent`, so the
  repo carries a pinned `@earendil-works/pi-tui: 1.5.2` dependency
  (`package.json`, resolved from the repo for `.pi/extensions/*` imports).
- Verified: extension loads in live pi; real model roundtrip clean.

Remaining polish (optional, low prio): colored left border, session-time
(e.g. " hunted 12 min"), request-counter for the current run.

Alignment update 2026-11-20: all UI insets unified at 1 col (editor pad
reverted 3→1 after tmux measurement showed prompt/outset mismatch; see
ISSUES.md row 20). Widget: Box(1,0) + 1-space separator row (Text("") renders
zero lines).

## 2. Session-scoped hunt workspace + executable session scripts — DONE (2026-11-20)

**Goal.** Every `/sepp <target>` gets its own durable workspace. Scripts,
tools, evidence and reports the agent produces during THAT hunt live there,
referenced and executable. Nothing is scattered in repo-level temp dirs.

Decisions (operator, 2026-11-20): ① session-id `YYYYMMDD-<hostslug>` ✓
② credentials stay a pointer into `data/targets/<slug>/target.json` ✓
③ retention: never automatic — only explicit `/prune-sepp-session` or
`sepp.sh --prune-workspaces[=DAYS] [--apply-confirm]` ✓
④ one workspace per host per day; multiple targets accumulate ✓

**Built:**

- `scripts/workspace.py` — `ensure|list|path|prune --days N [--apply]`;
  merges by host+day (second `/sepp` same host/day adds a target, keeps dir),
  validates `prune` against dated prefixes, root override via
  `SEPP_WORKSPACES_DIR`.
- `SEPP_WORKSPACES_DIR` env (default `data/workspaces/`, gitignored) — set
  in `.env` to relocate.
- `.pi/extensions/sepp.ts` — `/sepp` creates/merges the workspace before
  briefing and puts the absolute path in the kickoff prompt + widget
  (`▤ session <id>` line); new commands:
  - `/sepps` — selectable list (status, targets, updated, size)
  - `/sepp-resume <session-id>` — injects session.json targets + latest
    report tail into a resume prompt
  - `/prune-sepp-session [days]` — interactive prune (dry-run → select → apply)
- `AGENTS.md` rule 8 — hard contract: all hunt output lands in the session
  workspace (`scripts/` executable + `--help` + README.md index, `tools/`,
  `evidence/`, `reports/`); global `helpers/|drivers/|scripts/` are shared —
  fix bugs there, never add hunt-specific one-offs; nothing hunt-related in
  repo-root temp locations.
- Widget persists workspace path across session reload (custom entry).

**Not fully proven:** `--apply-confirm` passed through a real `sepp.sh` exec
(flag parsing proven via `--setup-only`; delete path proven via
`workspace.py --apply` directly). `/sepp-resume` types compile + extension
loads in live pi, but a real resume hasn't been exercised yet.

## 3. garak full battery — OPEN (was: post-canary)

Blocked-by-note: canary (`dan.DAN_Jailbreak`, garak 0.17 renamed the probe) still awaiting fresh target
credentials (operator must browse pilot-app.example through the proxy once).
After canary passes: widen probe set gradually (`injection`, then `xss`),
watching burn-thread workaround cost (fresh conv per probe ⇒ real pacing 10s ⇒
battery runtime grows linearly; pick probes accordingly).

## 4. Caido → pi bridge: right-click request dispatch — DONE (2026-11-21)

Operator decisions: option A (spool → live-TUI injection) · full spool
content (meta + raw request bytes) · enqueue immediately while the agent is
busy · built immediately. Origin: six2dez/dispatch (MIT) for scaffolding;
sender now FIRST-PARTY (`plugins/sepp/`, Makefile-packaged) after the
operator required source + packaging in this repo. Naming churn settled as SEPP
(Somebody Else's Problem) — global command `sepp`, plugin id `sepp`, menu entry
"SEPP: send to Hunt"; earlier iterations (send-to-pi / send-to-hunt /
babel-drop / hunt-call) are history.

**Flow:**
```
Caido HTTP History ─ right-click ─ "SEPP: send to Hunt"   (plugins/sepp)
  → backend: sdk.requests.get(id) → url/method/cookies + raw BYTES → temp file
  → spawn scripts/sepp.sh <url> <method> <raw> <cookies> <host> <port> <scheme> <path> <query>
    → data/spool/<id>.json + <id>.raw  (atomic tmp→rename, raw copied FIRST)
  → .pi/extensions/sepp.ts fs.watch (300ms debounce, exactly-once consume)
    → pi.sendUserMessage(...)  queued into the live TUI, even mid-run
```

**Details:**

- Plugin: one frontend (commands + RequestRow/Request menu entries + toasts,
  no UI framework) and one backend (field extraction + spawn). SDK deps
  pinned to dispatch's known-good versions (@caido/sdk-* 0.41.0, vite 5.2.7).
- Build: repo-root `make plugin-build` → `plugins/sepp/dist/plugin_package.zip`; global
  receiver via `make install-cli` → `sepp` on PATH (login-shell resolved)
  (typed-checked ESM bundles; zip layout identical to dispatch's pack step).
- dispatch no longer required: no Add-Tool dance, no binary download; the
  ~/Downloads copy (signature-verified v0.4.0) stays only as reference.
- Message framing tells the agent: fold into current hunt, read-only
  orientation first, the drop itself does NOT authorize state-changing traffic.
- Multi-select bursts coalesce into ONE message listing every request.
- Drops arriving while pi is closed are recovered on next `session_start`.
- Failure gotchas recorded in ISSUES.md row 22 (`caido:plugin` ambient types
  ship only with sdk-backend; DefineAPI<(sdk, ...args)> callback contract;
  frontend SDK type is `Caido<T, E>` from `@caido/sdk-frontend`).
- Sender-side proven end-to-end (script + watcher + live pi TUI injection).
  Bundle typechecks and packs; the in-Caido GUI click itself awaits install.

## 5. Tooling nits — OPEN (low)

- `.pi/extensions/sepp.ts` cred-pointer — DONE 2026-11-22 (was hardcode; now
  `data/targets/${CAIDO_TARGET:-testbed}/target.json`, see ISSUES row 41);
  remaining nicety: a `data/targets/index.json` host→pointer map.
- caido-cli `history -n` pagination quirk above 1000 entries (worked around via
  HTTPQL filter; revisit if a hunt ever needs deep unfiltered paging).
- Legacy `traffic/` probe scripts: superseded by session workspaces (Issue 2);
  migrate or delete on next encounter.
- Pre-rename path-rot sweep (ISSUES.md row 44): before/after renaming
  `~/projects/sep` → `~/projects/sepp`, audit `.venv-garak/bin/*` shebangs,
  `~/.local/bin/sepp`, and any absolute repo paths in configs/scripts.
