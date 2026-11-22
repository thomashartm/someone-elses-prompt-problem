# Borrowed-idea survey for sepp-redteam

**Date:** 2026-11-22
**Purpose:** Inputs for designing `sepp-redteam`, our single-file pi extension that replaces
the third-party `pi-redteam` npm package (2 tools: `redteam_coordinate`, `analyze_vulnerability`;
plus `/attack` and `/report`). Our goal: a structured, **evidence-required** note/state layer for
the SEPP hunting agent (findings, phase tracking, report generation) — not autonomous scanning.

Sources inspected (live web research on 2026-11-22): full source of the dropped `pi-redteam`
extension (`extensions/redteam.ts`, v1.0.5), Strix `strix/report/state.py` + `strix/report/sarif.py`,
NeoTheCapt/RedteamAgent README, polite-007/pi-redteam-tool repo tree + README, nuclei v3 output
structs, SARIF 2.1.0 (via Strix's emitter design notes), Claude Code TodoWrite payload format.
Local cross-reference: our own `helpers/findings.py` ledger.

---

## Summary table

| # | Project / Spec | What to steal | Effort |
|---|----------------|---------------|--------|
| 1 | Strix (usestrix/strix) | Finding field set incl. `assumptions`/`counterevidence`/`confidence_rationale`/`http_exchange_ids`; monotonic never-reused IDs; update-history + dependent-field dropping; dedup fingerprints; coverage-as-"pass" | Medium-high (biggest payoff) |
| 2 | NeoTheCapt/RedteamAgent | Engagement file layout (`findings.md`, `surfaces.jsonl`, `intel.md`), phase flow + manual phase overrides, scope-guard hook, resume-from-session-events | Low |
| 3 | pi-redteam (canoozie — package we drop) | Baseline to beat: tool schemas, TUI `renderCall`/`renderResult`, state reconstruction from session entries; fix its flaws (optional evidence, simulated analysis, in-memory state) | Zero (counter-example) |
| 4 | nuclei result JSON | `matcher-status` boolean gate; reproducible `curl-command` per result; `info.classification` (CVE/CWE/CVSS) normalized on write | Low |
| 5 | SARIF 2.1.0 (OASIS) | rule-vs-result split, required location, `partialFingerprints`, collapsing 5 severities onto 3 levels, synthetic-location escape hatch | Low (schema ideas only) |
| 6 | Claude Code TodoWrite | Exactly-one-item-`in_progress` invariant; `pending → in_progress → completed` transitions; `activeForm` display strings | Low |
| 7 | HackerOne / Bugcrowd VRT | Required narrative fields (steps-to-reproduce, impact, supporting material); P1–P5/VRT taxonomy anchoring severity to a published rubric | Low |
| 8 | OWASP WSTG / PTES | Stable checklist IDs (`WSTG-ATHZ-04`…) as finding-class taxonomy + phase coverage accounting | Low |
| 9 | polite-007/pi-redteam-tool | Settings pattern (`~/.pi/agent/settings.json` grouped per tool, one locator env var); one-tool-per-folder with `node --test` unit tests | Low |
| 10 | volt-l18/llm-redteam-pipeline | Separate "judge" role validating attacker claims — analog: evidence-validation pass before a finding may be marked `proven` | Idea only |

---

## Detailed sections

### 1. Strix (usestrix/strix) — the best single donor

Open-source AI pentest agent (Python/Agents SDK). Two files repay study:
`strix/report/state.py` (finding lifecycle) and `strix/report/sarif.py` (interchange emitter).

**Ideas worth stealing:**

- **Field vocabulary.** A Strix vulnerability report carries: `id` (`vuln-0001`…), `title`,
  `severity`, `timestamp`, `description`, `impact`, `target`, `technical_analysis`,
  `poc_description`, `poc_script_code`, `remediation_steps`, **`evidence`**,
  **`assumptions`**, **`counterevidence`**, **`confidence`** + **`confidence_rationale`**,
  **`severity_change_conditions`**, `fix_effort`, `cvss` + `cvss_breakdown`, `endpoint`,
  `method`, `cve`, `cwe`, `code_locations`, **`http_exchange_ids`**, `finding_class`,
  `agent_id`/`agent_name`. The trio `assumptions` / `counterevidence` / `confidence_rationale`
  is the anti-confabulation device: the agent must state what it *did not* verify and why it
  believes what it believes. `http_exchange_ids` is literally our Caido `request_id` — a list
  of captured transaction IDs the finding rests on.
- **Lifecycle discipline.** Update keeps the finding ID; appends an `update_history` entry
  (timestamp, changed fields, reasons, previous severity/confidence). Dependent fields are
  dropped when their primary changes without replacement (e.g. changing `severity` discards a
  stale `severity_change_conditions`; changing `confidence` discards `confidence_rationale`) so
  a finding can't assert "confidence: high" beside rationales explaining the *old* state.
  Deleted findings are tombstoned with reason + who/when, and **IDs are never reissued**
  (`vuln-0001` deletion bumps the allocator, preventing accidental overwrite).
- **Dedup fingerprints.** Stable identity computed from *primitives, not LLM prose*: hash of
  `(ruleId=CWE-normalized, uri+startLine or method+endpoint route)`, plus a "class fingerprint"
  from a closed curated vuln-class keyword list so the same bug class survives renames. Dedup
  keys must never hash agent-generated free text.
- **SARIF emission design.** Rules keyed on normalized `CWE-NNN` (fallback CVE → id → slug);
  5 severities collapse onto SARIF's 3 levels with the raw label surviving in
  `properties.<vendor>.*`; coverage entries ride in the same document as `kind: "pass" /
  "notApplicable" / "open"` results so "nothing found here, here's why" is a first-class
  exported fact; findings without a safe location get a flagged synthetic anchor
  (`SECURITY.md` + `synthetic_location: true`) instead of being dropped; completeness caveats
  become `invocations[].toolExecutionNotifications` so a truncated run can't masquerade as clean
  (`executionSuccessful: false`).
- **Persistence stance.** Corruption of the findings JSON is surfaced loudly on resume rather
  than wiped ("silently swallowing a corrupt `vulnerabilities.json` would let the next vuln
  allocate `vuln-0001` and overwrite prior findings" — their comment, their scar tissue).

**Adaptation for sepp-redteam:** adopt nearly the entire finding field set; implement
`update_history` + dependent-field dropping + never-reused IDs exactly; use a class hash of
`(classKeyword, method, endpoint)` as our dedup key; treat a failed finding-save as fatal to that
`create` call, not swallowed. Our `confidence` enum stays SEPP's `proven | probable | hunch`
(the report language our AGENTS.md already mandates), with `confidence_rationale` added.
Map `http_exchange_ids` to Caido request IDs (compat with `helpers/findings.py`'s mandatory
`request_id`).

---

### 2. NeoTheCapt/RedteamAgent — engagement state & phases

Multi-CLI (Claude Code / OpenCode / Codex) pentest agent: 8 subagents, Dockerized Kali tools,
SQLite case queue, orchestrator GUI. Config as data, not magic.

**Ideas worth stealing:**

- **Engagement directory convention.** Everything an engagement produces lives under
  `engagements/<timestamp-target>/`: `findings.md`, `report.md`, `log.md`, `intel.md`,
  `intel-secrets.json`, `auth.json`, `cases.db`, `surfaces.jsonl`. `surfaces.jsonl` tracks
  high-risk surface coverage line-by-line — cheap, greppable, append-only.
- **Phase flow with escapes.** Five phases (Recon → Collect → Test → Exploit+OSINT → Report)
  enforced for the autonomous run, but surfaced as **manual override commands** (`/recon`,
  `/scan`, `/enumerate`, `/exploit`, `/pivot`) — humans can jump the state machine; the phase
  marker still records reality. `Test` deliberately abandons strict phase gates for a
  **stage-based pipeline**: each queued case carries a `stage` column independent of `status`
  (confirmed → exploit track, fuzz_pending → fuzz track) — decoupling "where in the workflow"
  from "what happened to it".
- **Per finding-item lifecycle statuses separate from phases** (`ingested`, `vuln_confirmed`,
  `fuzz_pending`, …) — a finding/entity's own state ≠ session phase. Direct counterexample to
  the dropped pi-redteam package folding everything into one 5-value phase enum.
- **Hardening that only exists because LLM agents lie/leak/die:** finding deduplication,
  *surface coverage enforcement*, auto-synthesis of missing report artifacts, auto-resume after
  stalls, and a **scope-check hook** that blocks/warns out-of-scope tool calls at the hook layer
  (their `.claude/settings.json` / `.opencode/plugins/engagement-hooks.ts`).
- **CLI compat matrix as design doc:** one source of truth for commands/agents, generated per
  CLI. Confirms our instinct that the extension (not the package) should hold zero hunt logic.

**Adaptation:** state file mirrors the engagement-dir idea at smaller scale — our workspace gets
`findings.json` (ledger, append-tolerant), `phase.json` (current phase + history), and reuse of
existing `evidence/`. Adopt their phase model minus Collect/Test splits (SEPP's hunting loop is
Orient → Map → Hypothesize → Prove → Record → Report, matching AGENTS.md). Adopt the
"stage independent of status" principle: a finding has `status` (draft/confirmed/withdrawn);
the *session* has `phase`.

---

### 3. pi-redteam (canoozie/redteam on Codeberg) — the package we drop

This IS the npm package being replaced (the "10 agents" README marketing overshoots what the
single-file extension actually does). Full source of `extensions/redteam.ts` reviewed.

**What it actually is:** a TypeScript pi extension registering exactly 2 tools —
`analyze_vulnerability` (note-taking sink: target/attackType/severity/evidence → fabricated
finding) and `redteam_coordinate` (action enum `initialize | spawn_agent | broadcast | gather |
status | complete` on an in-module `RedTeamState`), plus `/attack start|phase|status` and
`/report` commands, TUI renderers, and hook plumbing.

**Data shapes to know (they set user expectations we should honor):**

```ts
interface VulnerabilityFinding {
  id: string; type: string; severity: "critical"|"high"|"medium"|"low";
  cvssScore?: number; endpoint?: string; description: string;
  evidence?: string; recommendation?: string;
  sourceAgent: string; timestamp: number;
}
interface RedTeamState {
  phase: "planning"|"recon"|"exploit"|"reporting"|"completed";
  target: string; startedAt: number;
  agents: { name; status: idle|running|completed|failed; lastUpdate; findings }[];
  sharedFindings: VulnerabilityFinding[];
  attackSurfaces: string[];
  coverage: { tested: []; pending: []; blocked: [] };
  coordinationTokens: Map<string,string>;
}
```

**What it got wrong (our checklist of things NOT to copy):**

- `evidence` is optional and never validated; the tool even generates findings **"simulated
  analysis (in real implementation, this would do actual testing)"** — findings without proof
  are a feature, not a bug. Direct opposite of SEPP's evidence requirement.
- State lives in module globals; reconstruction from session entries reads back most fields but
  `coordinationTokens` (a `Map`) is silently dropped, and there is no durable on-disk state at
  all — a crash loses every finding.
- Phase enum conflates agent-spawn bookkeeping, session progress, and report readiness.
- `/report` prints a severity-count notification to the TUI and does nothing else; no file, no
  format, no sorting.
- Duplicate `before_agent_start` registrations with overlapping responsibilities; dead
  "dynamic tool management" only triggered by literal prompt substrings like `/recon-only`.

**Ideas worth stealing (positives):**

- **TUI discipline**: `renderCall` collapses to one colored line, `renderResult` expands with
  severity-colored headers — worth porting so our finding tool renders nicely in pi.
- **Session-entry state reconstruction** on `session_start`/`session_tree`: state survives
  session branching by replaying tool-result details from the branch. Nice pi-idiomatic belt to
  go with our on-disk state file.
- **State piggy-backing**: storing the whole state object as tool-result `details` (and `state`
  field) on every coordinate call gives free session-journal persistence.
- TypeBox `Type.Object` parameter schemas + `StringEnum` for tight, describable tool inputs.

**Adaptation:** keep the two-tool mental surface (`sepp_note_finding`, `sepp_phase`), keep render
patterns and session reconstruction, replace everything else. Command surface: upgrade from
their 4 commands to a coherent set (below). Make evidence mandatory at `proven`.

---

### 4. Nuclei (projectdiscovery) result model — proof discipline

**Ideas worth stealing:**

- **`matcher-status` boolean.** A nuclei "finding" is `matched` because a deterministic
  condition evaluated true against the stored response — the match verdict travels *with* the
  result. Analog: our finding carries the evidence-verdict explicitly (`matcherStatus`-style
  `verified: boolean`) derived from whether the claimed observable difference actually appeared
  in the captured responses.
- **Reproducible `curl-command` on every result.** Every hit ships the exact replayable
  command. Cheapest possible anti-confabulation: no "trust me, it worked", only "here is the
  byte-accurate replay". With Caido, ours becomes the **request ID pair + `caido-cli raw`
  replay command**.
- **Classification normalized at write time:** `info.classification.{cve-id,cwe-id,cvss-score}`
  with severity from a fixed rubric — malformed taxonomy rejected early, so downstream dedup
  (CWE-keyed, à la Strix) works.

**Adaptation:** every SEPP finding's evidence entry must carry either a Caido request(-pair) ID
or a replay command; the `create` tool validates presence, and `proven` additionally demands a
before/after pair. PoC command text is stored verbatim, never LLM-paraphrased.

---

### 5. SARIF 2.1.0 (OASIS) — what an interchange format forces you to admit

Relevant portions (validated against the schema in Strix's tests):

- **Split "rule" (the vulnerability class) from "result" (the instance).** Rule = CWE-backed
  definition + default level; Result = occurrence + level + message + **at least one location**
  + optional fingerprints/fixes. Forcing a location prevents anonymous findings.
- **`partialFingerprints`** make cross-run identity an *explicit contract* rather than fuzzy
  title matching — directly reusable as our dedup key.
- **Limited severity vocabulary is a feature** (3 levels) — richer ratings survive only as
  namespaced properties. Keeps strict consumers functional while preserving nuance.
- **Escape hatches for awkward truths**: `synthetic_location` for findings without a real anchor,
  `kind: pass/notApplicable/open` for negative results, `executionSuccessful: false` +
  notifications for incompleteness. An honest tool can express "I looked, found nothing, and
  I wasn't finished" — exactly the reporting honesty SEPP's operator wants.

**Adaptation:** names for our ledger fields shouldn't fight SARIF so a future exporter is
trivial: `severity` (5-label), `location {endpoint, method}`, `class` ↔ rule id (CWE when
known), dedup fingerprint field present from day one even if only used internally.

---

### 6. Claude Code TodoWrite — micro phase-state machine

Payload: `todos: [{ content, status: "pending"|"in_progress"|"completed", activeForm }]`
(and the newer Task tracker adds `subject`, `description`, optional `priority`, `blocks`).

**Ideas worth stealing:**

- **Invariant: exactly one item `in_progress` at a time**; `pending → in_progress → completed`
  ordering; completions earned, never defaulted ("keep in_progress when there are errors").
- **`activeForm`** — a present-tense display string per task ("Mapping endpoints") kept separate
  from the task content. Our `/hunt status` output should read off `activeForm`s.

**Adaptation:** encode our six phases with the same transition algebra, in a `phase.json` the
extension rewrites atomically. Gate transitions on predicates (e.g. entering `prove` requires a
logged hypothesis; entering `record` requires ≥1 evidence-bearing finding draft).

---

### 7. HackerOne / Bugcrowd VRT — narrative requirements + severity rubric

- **HackerOne** reports require structured fields: title, vulnerable asset (scoped), weakness
  class, `severity_rating`, `structured_scopes`, and crucially `vulnerability_information` with
  explicit **steps to reproduce** and **impact** statements, plus **supporting materials**
  (attachments/screenshots). Notably triagers fight *invalid reproductions* as much as invalid
  reports — the field set exists precisely so claims are auditable.
- **Bugcrowd VRT** publishes a vulnerability rating taxonomy (P1–P5) mapping bug classes to
  severities via decision trees. Stealing the *idea*: severity isn't vibes — it's chosen from a
  published rubric tied to the finding class; our class→severity suggestions table can crib its
  shape.

**Adaptation:** `create` requires, for any finding, `impact` (one sentence — our report format
already demands this) and `poc`; `severity` suggests itself from `class` via a small static
table we can tune (not decided: whether we enforce class-consistent severity or merely warn).

---

### 8. OWASP WSTG / PTES — stable taxonomy and phase canon

- **WSTG checklist IDs** (`WSTG-INPV-02`, `WSTG-ATHZ-04`, …) give stable, human-readable class
  identifiers with published test methodology — perfect as `Finding.class` values, and as the
  axis for coverage accounting ("surfaces × checklist sections attempted").
- **PTES** defines recognized reporting phases (intelligence gathering → threat modeling →
  vulnerability analysis → exploitation → reporting) — external validation that our
  Orient/Map/Hypothesize/Prove/Record loop, padded with Report, maps to reviewer-expected
  structure. Nuclei-template-style `classification: {cve, cwe, cvss-score, tags}` completes the
  crosswalk.

**Adaptation:** `Finding.class` SHOULD be a WSTG id when applicable, else a slug from Strix's
curated vuln-class keyword approach (closed list, not freeform).

---

### 9. polite-007/pi-redteam-tool — packaging hygiene (minor)

Pure recon-toolset pi extension (DNS/FOFA/httpx/whois tools; no state layer). One thing worth
copying: **configuration lives in one place** — `~/.pi/agent/settings.json` under a single
`redteam` key grouped per tool, with `PI_SETTINGS_PATH` as the sole env var *locating* config
(not carrying it). Their stated principle: "one source means the key, proxy or path a tool uses
is always the one the file names." Also: one extension per folder with co-located `*.test.mjs`
unit tests run by plain `node --test` — CI-cheap and applicable to our single file (put the
schemas/validation in testable pure functions).

---

### 10. volt-l18/llm-redteam-pipeline — judge separation (idea only)

Attacker model vs target model with an independent LLM-as-judge scoring transcripts. Relevant
only as an architectural echo: **claims and their adjudication run through different channels.**
SEPP's equivalent is structural rather than ML: the agent's *claim* goes through the finding
tool; the *verdict* comes from captured HTTP transaction pairs in Caido that the extension (or
operator) can re-fetch and check. Never let the claiming agent also grade itself in-band.

---

## Recommended Finding schema (TypeScript, ready to implement)

```typescript
// ---------- enums ----------
export type Severity =
  | "critical" | "high" | "medium" | "low" | "info";

/** SEPP confidence ladder (matches AGENTS.md + helpers/findings.py). */
export type Confidence = "proven" | "probable" | "hunch";

export type FindingStatus = "draft" | "confirmed" | "withdrawn";

// ---------- evidence ----------
/**
 * One piece of immutable backing. At least one is REQUIRED at create time;
 * `confidence: "proven"` additionally requires >=1 ENTRY with beforeId+afterId
 * (nuclei "matcher-status" analogue: an observed before/after diff).
 */
export interface EvidenceRef {
  kind: "caido-request" | "http-pair" | "file" | "command";
  requestId?: string;        // Caido request id (helpers/findings.py anchor)
  beforeId?: string;         // legit request id  (http-pair form)
  afterId?: string;          // manipulated request id
  /** Replayable PoC verbatim (nuclei `curl-command` analogue). */
  command?: string;          // e.g. `caido-cli request 12345`
  /** Artifact path inside the session workspace (evidence/, report excerpt). */
  file?: string;
  /** sha256 of response snippet/diff — detects later re-verification drift. */
  digest?: string;
  observedAt: string;        // ISO-8601
  note?: string;             // one-line interpretation (the "diff explanation")
}

// ---------- finding ----------
export interface Finding {
  /** `VULN-####`, monotonic; deleted ids are NEVER reissued (Strix rule). */
  readonly id: string;
  title: string;                       // single-line, control chars collapsed (Strix)
  /** Stable class: WSTG-* id, else CWE-NNN, else curated keyword slug. Drives dedup. */
  class: string;
  cwe?: string;                        // normalized "CWE-NNN" (SARIF/Strix interop)
  severity: Severity;
  confidence: Confidence;
  status: FindingStatus;

  // The claim
  description: string;
  impact?: string;                     // one sentence (HackerOne discipline)
  endpoint?: string;                   // SARIF logicalLocation analogue
  method?: string;                     // HTTP method for the route
  poc?: { narrative: string; command?: string };  // command stored verbatim

  // The anti-confabulation quartet (Strix)
  evidence: EvidenceRef[];             // >=1 unless status === "draft"
  assumptions?: string;                // what was NOT verified
  counterevidence?: string;            // observations arguing against the claim
  confidenceRationale?: string;        // WHY this confidence level

  // Rubric + rating (CVSS optional; severity_change_conditions mandatory if severity contested)
  cvss?: number;
  cvssBreakdown?: Record<string, string>;
  severityChangeConditions?: string;   // what would bump/shrink the severity

  remediation?: string[];

  // Provenance & history
  reportedBy: string;                  // agent/session identifier
  createdAt: string;
  updatedAt?: string;
  updateHistory?: Array<{              // Strix-style journal
    at: string;
    changedFields: string[];
    droppedFields?: string[];          // dependents removed by the change
    previousSeverity?: Severity;
    previousConfidence?: Confidence;
    reason?: string;
  }>;

  /** Internal dedup key: sha256(`${class}|${method} ${endpoint}`). Strix-fingerprint analogue. */
  fingerprint?: string;
}
```

**Enforced rules (validation layer, not vibes):**

1. `create` with `confidence: "proven"` and zero evidence refs → reject with actionable error.
2. `create` with `confidence: "proven"` and no evidence item bearing both `beforeId`+`afterId`
   → reject ("proven requires a before/after pair").
3. Updates keep `id`; appending `updateHistory` is mandatory when any field changes; dependents
   dropped when their primary changes without replacement (Strix `_DEPENDENT_REPORT_FIELDS`
   semantics).
4. Withdrawal tombstones the finding (status `withdrawn` + reason + who), frees nothing —
   ids never recycled (Strix/Strix id-allocator scar tissue).
5. Ledger writes: atomic (temp file + rename, SARIF-emitter pattern); corruption on resume is
   raised loudly, not silently reset (Strix hydration stance).
6. Field names deliberately aligned with SARIF concepts (`severity`, location triplet,
   `fingerprint`) so a future `export sarif` command is mechanical.

## Recommended phase model (TypeScript, ready to implement)

```typescript
export type Phase =
  | "orient"       // browse Caido history (read-only, zero new target traffic)
  | "map"          // enumerate routes/params
  | "hypothesize"  // name candidate weaknesses + predicted observable differences
  | "prove"        // run cheapest experiments; collect before/after pairs
  | "record"       // register evidence-backed findings
  | "report";      // generate/close the session report

/** Gates: predicate required to ENTER the phase (evaluated by the tool, not the LLM's honor). */
export interface PhaseDef {
  name: Phase;
  activeForm: string;                 // TodoWrite-style display: "Mapping endpoints…"
  gate?: (state: HuntState) => true | string;  // true = ok, string = blocking reason
  /** Legal successor phases (explicit, small, cyclic where the loop needs it). */
  next: Phase[];
}

export interface HuntState {
  phase: Phase;                        // exactly ONE active at a time (TodoWrite invariant)
  target: string;
  startedAt: string;
  hypotheses: Array<{
    id: string; claim: string;
    prediction: string;                // the observable difference (cheap experiment chosen)
    status: "open" | "confirmed" | "disproven";
    findingIds?: string[];             // link through to the ledger
  }>;
  surfaces: string[];                  // orient/map output (RedteamAgent surfaces.jsonl spirit)
  coverage: { tested: string[]; pending: string[]; blocked: string[] };  // 1:1 with dropped pkg
  counters: { requestsSent: number; burstsPaused: boolean };             // rate-budget optics
}

export const PHASES: Record<Phase, PhaseDef> = {
  orient:      { name: "orient",      activeForm: "Reading proxy history",   next: ["map"] },
  map:         { name: "map",         activeForm: "Enumerating attack surface",
                 gate: s => s.surfaces.length > 0 || "orient produced no surfaces",
                 next: ["hypothesize", "orient"] },
  hypothesize: { name: "hypothesize", activeForm: "Forming hypotheses",
                 gate: s => s.hypotheses.some(h => h.status === "open") ||
                            "declare a falsifiable hypothesis first",
                 next: ["prove", "map"] },
  prove:       { name: "prove",       activeForm: "Running proving experiments",
                 gate: s => s.counters.requestsSent < 50 || "burst budget exhausted, pause",
                 next: ["record", "hypothesize", "prove"] },  // disproved → back to hypothesize
  record:      { name: "record",      activeForm: "Recording findings",
                 gate: s => (s.hypotheses.some(h => h.status === "confirmed")) ||
                            "prove at least one hypothesis before recording",
                 next: ["hypothesize", "prove", "report"] },
  report:      { name: "report",      activeForm: "Writing the report",
                 gate: s => true, next: [] },
};
```

**Operational rules layered on top:**

- Phase flips only through the tool; the extension writes `phase.json` atomically and stamps a
  history line (Mirrors TodoWrite "one in_progress", RedteamAgent's phase log).
- Back-edges are legal and encouraged (`prove → hypothesize` on a disproven claim,
  `record → prove` when review finds a hole) — unlike the dropped package's linear 5-step ladder.
- The dropping of the strict single-successor constraint is deliberate: the SEPP loop is a
  funnel with feedback, not a waterfall (RedteamAgent "stage ≠ status" insight applied at
  session granularity).

## Recommended command surface

| Command | Behavior | Lineage |
|---|---|---|
| `/hunt start <target>` | init `phase.json` + findings ledger in session workspace, phase=`orient` | dropped pkg `/attack start` |
| `/hunt status` | phase, activeForm, per-severity counts, open hypotheses, coverage triad | dropped pkg + RedteamAgent `/status` |
| `/hunt phase <name>` | gated transition; prints blocking reason when the gate fails | dropped pkg, upgraded |
| `/finding create` | tool (not command): validated, appends ledger + optional Caido push via existing `helpers/findings.py` contract | dropped pkg, de-simulated |
| `/finding update <id>` | journaling update w/ dependent-field semantics | Strix |
| `/finding withdraw <id> <reason>` | tombstone, never delete | Strix |
| `/report` | emits ranked markdown report per AGENTS.md format; sorts by severity; embeds PoC request IDs (never full tokens) | dropped pkg, actually implemented |

## Explicitly rejected ideas (with reasons)

- **In-memory state or Map-valued state** (dropped pkg): crashes lose findings; Maps survive
  session replay poorly. On-disk JSON + session-entry reconstruction instead.
- **Simulated/example finding generation** (dropped pkg): violates Honesty rule 0; findings
  only exist as recorded assertions about real transactions.
- **Multi-agent spawn/broadcast machinery** (both pi packages): our architecture delegates via
  the controller/hunter split (AGENTS.md); the extension must not grow orchestration opinions.
- **CVSS enforcement**: scores decay into false precision for LLM-entered vectors; keep `cvss`
  optional, rely on the 5-label rubric + severity-change conditions.
