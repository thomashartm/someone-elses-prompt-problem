# sepp-redteam — gap plan (v1 shipped → v2 researched)

Companion to `docs/research-redteam-designs.md` (source survey) and
`../redteam-review/REVIEW-NOTES.md` (why we forked). Written 2026-11-22.

## What v1 already is (`.pi/extensions/sepp-redteam.ts`, shipped today)

Fork-and-fix of pi-redteam 1.0.5 on the current pi API:

| Kept from upstream | Fixed / changed |
|---|---|
| 2-tool shape (sink + tracker) | Sink no longer "analyzes" or confirms — stores exactly what the model cites; warns on narration-style evidence |
| TypeBox schemas, TUI renderers, session-entry state | `typebox` (Earendil), not `@sinclair/typebox`; strict-mode clean vs installed pi typings |
| /attack,/report idea | `/sred status|start|phase|report`; bare-command parse bug fixed; persistent output + widget instead of 2 s toasts |
| phases + coverage trio | Phase ladder re-aligned to the AGENTS.md loop; spawn/broadcast/gather machinery dropped (delegation lives in AGENTS.md, not in an extension) |

## Gap analysis vs the researched ideal

Ranked by how much confabulation-risk or agent-coordination pain each removes.

| # | Gap in v1 | Research source | v2 design (already sketched in research doc §Recommended) |
|---|---|---|---|
| 1 | Hunters are SEPARATE processes — they cannot see session-entry state; ledger is controller-bound | RedteamAgent engagement files | ✅ **PARTIALLY CLOSED in v1.1 (2026-11-22, operator gave go)**: dual-write to disk (`phase.json` + `findings.json`, atomic temp+rename; `$SEPP_REDTEAM_LEDGER_DIR` → workspace → `data/sepp-redteam/`), hydrate-on-start, corruption announced loudly. Remaining for v2: hunter-side APPEND semantics + merge/dedup so concurrent writers don't clobber (current `findings.json` is a mirror, last-writer-wins) |
| 2 | "proven" rests on a soft warning; evidence is one free-text string | Nuclei matcher-status; Strix exchange IDs | Structured `EvidenceRef[]`: `proven` HARD-REQUIRES a before/after Caido request-ID pair at create time (reject with actionable error), PoC command stored verbatim |
| 3 | status field conflates confidence and lifecycle | Strix; HackerOne flow | Split: `confidence: proven/probable/hunch` × `status: draft/confirmed/withdrawn`; withdrawal tombstones (ids never reused) |
| 4 | No dedup — model can log the same issue 5× | Strix fingerprints | `fingerprint = sha256(class \| method endpoint)` on create; duplicates merge into existing finding's evidence list |
| 5 | No history — a mutated finding forgets its past | Strix update journal | `updateHistory[]` appended on every change; severity/confidence downgrade drops stale dependent fields |
| 6 | Hypotheses invisible — only findings exist | research phase model; WSTG | `hypotheses[]` with prediction + open/confirmed/disproven; confirmed hypothesis links findingIds |
| 7 | Phases are honor-system jumps | TodoWrite invariant | Tool-evaluated GATES (record requires ≥1 confirmed hypothesis; prove requires budget) with explicit blocking reasons + legal back-edges |
| 8 | Report omits per-finding PoC | HackerOne VRT | Report embeds replay command (`caido-cli request <id>`) per finding, ranked, request-IDs only (secrets hygiene) |
| 9 | Ledger and helpers/findings.py are strangers | (our own contract) | `record` optionally pushes via `helpers/findings.py create` (single source of truth in Caido) |
| 10 | No interchange format | SARIF | (optional, later) `export sarif` — field names already aligned to make it mechanical |

## Roadmap

**v1.1 — anti-confabulation core (half a day, no breaking UI change)**
Gaps 2 + 8 + monotonic persisted ID counter (`VULN-####`, Strix scar-tissue rule).
Hard pair-validation is the single highest-value change: it converts AGENTS.md
rule 4 from documentation into mechanism.

**v2 — shared-ledger + memory (1 day)**
Gaps 1 + 3 + 4 + 5 + 7. On-disk dual-write, confidence/lifecycle split,
dedup, journal, gated phases. After this, hunter subagents get
"append to the workspace ledger" superpowers and the controller's /sred
widget reflects THEIR work.

**v3 — integration polish (opportunistic)**
Gaps 6 + 9 + 10 (hypothesis model, findings.py bridge, SARIF export).

## Decisions needed (from the operator)

— ALL FOUR RESOLVED 2026-11-22 (operator):

1. ~~`/sred` or `/hunt`~~ → **`/hunt`**, PLUS a full-engagement mode:
   `/hunt here <url>` arms the ledger AND injects the in-this-session red-team
   kickoff (real tools unlocked — the honest remake of upstream `/attack start`,
   with scope/rate/approval rules spelled out and the ledger kept secondary so
   the row-32 fabrication trap cannot recur).
2. ~~`done` phase~~ → folded; 6 phases, `report` is terminal.
3. ~~uninstall npm pi-redteam~~ → done (`pi uninstall npm:pi-redteam`, marker
   verified gone; ISSUES-row-28 discipline kept — `</dev/null`).
4. ~~Shared-ledger location~~ → **implemented the same day (v1.1)**, flexible as
   ordered: first match wins — `$SEPP_REDTEAM_LEDGER_DIR` (operator override) →
   active session workspace (when `/sepp` ran) → `data/sepp-redteam/`. Writes are
   atomic; a corrupt ledger is announced loudly and never silently reset; a
   fresh session hydrates from disk when it carries no ledger of its own.
   Hunters APPENDING safely (merge, not clobber) remains v2 scope.

## Done-when for v1 (today)

- [x] Strict typecheck vs installed pi typings (exit 0; negative control exits 2)
- [x] Dependency removal (setup loop + README) + npm package uninstalled
- [x] Decision 4 executed: disk ledger shipped EARLY (dual-write + hydrate +
      corrupt-loud); `$SEPP_REDTEAM_LEDGER_DIR` override documented
- [x] RUNTIME smoke via stubbed pi harness (3 modes: happy / restore /
      corrupt — all green: disk files land in workspace, bare /hunt speaks,
      report written, restore announces, corruption never silent)
- [ ] LIVE smoke in real pi (formality: jiti maps `typebox`; expectation set)
