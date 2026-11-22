# Findings Ledger & Phase Model — Design Notes

Target design for SEPP's structured findings ledger and hunt phase tracker (`.pi/extensions/sepp-redteam.ts` + workspace ledger files).

## 1. Finding Schema & Evidence Rules

Separate **confidence** (`proven` | `probable` | `hunch`) from **lifecycle status** (`draft` | `confirmed` | `withdrawn`), and enforce evidence requirements in the tool layer rather than relying on prompts.

```typescript
export type Severity = "critical" | "high" | "medium" | "low" | "info";
export type Confidence = "proven" | "probable" | "hunch";
export type FindingStatus = "draft" | "confirmed" | "withdrawn";

export interface EvidenceRef {
  kind: "caido-request" | "http-pair" | "file" | "command";
  requestId?: string;        // Caido request ID
  beforeId?: string;         // baseline request ID (http-pair)
  afterId?: string;          // manipulated request ID (http-pair)
  command?: string;          // replayable PoC command (e.g. caido-cli request <id>)
  file?: string;             // artifact path inside session workspace
  digest?: string;           // sha256 of response snippet/diff
  observedAt: string;        // ISO-8601
  note?: string;             // one-line diff explanation
}

export interface Finding {
  readonly id: string;       // monotonic VULN-####; never reused after withdrawal
  title: string;             // single-line
  class: string;             // OWASP LLMxx, WSTG-*, CWE-NNN, or slug (drives dedup)
  cwe?: string;              // normalized "CWE-NNN"
  severity: Severity;
  confidence: Confidence;
  status: FindingStatus;

  description: string;
  impact?: string;           // one sentence
  endpoint?: string;
  method?: string;
  poc?: { narrative: string; command?: string };

  // Anti-confabulation fields
  evidence: EvidenceRef[];   // >= 1 required unless status === "draft"
  assumptions?: string;      // what was not verified
  counterevidence?: string;  // observations arguing against the claim
  confidenceRationale?: string;
  severityChangeConditions?: string;

  remediation?: string[];
  reportedBy: string;
  createdAt: string;
  updatedAt?: string;
  updateHistory?: Array<{
    at: string;
    changedFields: string[];
    droppedFields?: string[];
    previousSeverity?: Severity;
    previousConfidence?: Confidence;
    reason?: string;
  }>;

  /** Dedup key: sha256(`${class}|${method} ${endpoint}`) */
  fingerprint?: string;
}
```

### Validation Rules

1. **Proven requires a pair**: creating or upgrading a finding to `confidence: "proven"` requires at least one `EvidenceRef` with both `beforeId` and `afterId`. Reject with an actionable error otherwise.
2. **Deduplication**: compute `fingerprint` on create; duplicate class + method + endpoint merges new evidence into the existing finding instead of creating duplicates.
3. **Update journal**: every mutation preserves `id`, appends an `updateHistory` entry, and drops stale dependent fields when severity or confidence changes without replacement.
4. **Tombstones**: withdrawing a finding sets `status: "withdrawn"` with a reason; IDs are never recycled.
5. **SARIF alignment**: keep `severity`, location (`method` + `endpoint`), `cwe`, and `fingerprint` aligned so a future `export sarif` command is mechanical.

## 2. Gated Phase Model

Phases mirror the SEPP loop (`orient → map → hypothesize → prove → record → report`) with tool-evaluated entry gates and explicit legal back-edges.

```typescript
export type Phase = "orient" | "map" | "hypothesize" | "prove" | "record" | "report";

export interface Hypothesis {
  id: string;
  claim: string;
  prediction: string;        // expected observable difference
  status: "open" | "confirmed" | "disproven";
  findingIds?: string[];
}

export interface HuntState {
  phase: Phase;
  target: string;
  startedAt: string;
  hypotheses: Hypothesis[];
  surfaces: string[];
  coverage: { tested: string[]; pending: string[]; blocked: string[] };
  counters: { requestsSent: number; burstsPaused: boolean };
}
```

| Phase | Entry gate | Legal next phases |
|---|---|---|
| `orient` | none (read-only Caido history) | `map` |
| `map` | `surfaces.length > 0` | `hypothesize`, `orient` |
| `hypothesize` | at least one `open` hypothesis | `prove`, `map` |
| `prove` | burst budget available (`requestsSent < 50`) | `record`, `hypothesize`, `prove` |
| `record` | at least one `confirmed` hypothesis | `hypothesize`, `prove`, `report` |
| `report` | none | terminal |

Back-edges are deliberate: disproven hypotheses loop `prove → hypothesize`; review gaps loop `record → prove`.

## 3. Shared Workspace Ledger

- **Atomic writes**: write `phase.json` and `findings.json` via temp file + rename into `$SEPP_REDTEAM_LEDGER_DIR`, the active session workspace, or `data/sepp-redteam/`.
- **Fail loud on corruption**: never silently reset a corrupt ledger on session hydrate.
- **Multi-hunter concurrency**: move from last-writer-wins mirror writes to hunter-side append + merge/dedup so concurrent hunter subagents safely contribute findings to the shared workspace ledger.

## 4. Planned Milestones

- **Anti-confabulation core**: hard before/after pair validation for `proven`, monotonic `VULN-####` IDs, per-finding `caido-cli request <id>` PoC commands in generated reports.
- **Shared ledger & gated phases**: confidence/lifecycle split, fingerprint dedup, update journal, withdrawal tombstones, hunter-safe append/merge, tool-evaluated phase gates.
- **Integrations**: first-class hypothesis tracking, optional push to Caido via `helpers/findings.py create`, and SARIF export.
