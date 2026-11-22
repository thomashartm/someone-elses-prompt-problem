/**
 * sepp-redteam — local pi extension (forked & fixed from npm pi-redteam 1.0.5).
 *
 * WHY THE FORK EXISTS (evidence in ISSUES.md rows 31–32 and
 * ../redteam-review/REVIEW-NOTES.md):
 *   - upstream `/attack start` locked the model onto two tools that do NO
 *     network I/O → it confabulated a 9-finding report against a host that
 *     does not resolve (a mistyped ghost host). Findings must never be born without
 *     evidence again.
 *   - upstream feedback was 2-second toasts only (silent-no-op feel); the
 *     bare-/attack parser bug made it literally a no-op.
 *   - upstream shipped pre-Earendil imports (@mariozechner/*, @sinclair/typebox)
 *     and multi-agent machinery (spawn/broadcast/gather) that never worked.
 *
 * DESIGN (SEPP-ified):
 *   - This extension is a LEDGER, not a scanner. The hunt (probe, reproduce,
 *     record in Caido) stays governed by AGENTS.md and the controller/hunter
 *     split. Here we only hold structured state:
 *       * phase: orient → map → hypothesize → prove → record → report → done
 *       * findings WITH mandatory evidence + status (proven/probable/hunch)
 *       * coverage: tested / pending / blocked
 *   - /hunt arm/inspect the ledger; /hunt here ALSO injects a full-engagement
 *     kickoff (the honest rebirth of upstream's /attack start: it unlocks the
 *     agent's real tools instead of locking it onto this ledger).
 *   - Two tools for the agent:
 *       sepp_record_finding — evidence-checked note-taking (the sink)
 *       sepp_coordinate     — phase + coverage bookkeeping (the tracker)
 *
 * Commands:
 *   /hunt                     — status (persistent, also shown in the widget)
 *   /hunt start <url> [notes] — arm/reset the ledger for a target
 *   /hunt here <url> [notes]  — arm + FULL RED TEAM IN THIS SESSION (kickoff)
 *   /hunt phase <name>        — jump the phase explicitly
 *   /hunt report              — write findings markdown into data/reports/
 */
import type {
	ExtensionAPI,
	ExtensionContext,
	Theme,
} from "@earendil-works/pi-coding-agent";
import { Box, Text } from "@earendil-works/pi-tui";
import * as fs from "node:fs";
import { Type } from "typebox";

const REPO = process.cwd();
const WIDGET_KEY = "sepp-redteam";
const STATE_ENTRY = "sepp-redteam-state";

/**
 * `{ type: "string", enum: [...] }` schema, statically typed — provider-safe
 * (Google & friends reject anyOf/const patterns). Same trick pi's own compat
 * shim uses; we inline it because the shim is not exported from the package.
 */
function stringEnum<const V extends readonly string[]>(
	values: V,
	options?: { description?: string; default?: V[number] },
) {
	return Type.Unsafe<V[number]>({
		type: "string",
		enum: values,
		...(options?.description && { description: options.description }),
		...(options?.default && { default: options.default }),
	});
}

// ----------------------------------------------------------------------------
// Types
// ----------------------------------------------------------------------------

type Phase = "orient" | "map" | "hypothesize" | "prove" | "record" | "report";
const PHASES: Phase[] = ["orient", "map", "hypothesize", "prove", "record", "report"];

type Severity = "critical" | "high" | "medium" | "low";
type Status = "proven" | "probable" | "hunch";

interface Finding {
	id: string;
	type: string;
	severity: Severity;
	status: Status;
	endpoint: string;
	claim: string;
	evidence: string;
	requestId?: string; // Caido request id — the anchor AGENTS.md demands
	recommendation?: string;
	recordedBy: string; // "controller" | "hunter:<name>"
	timestamp: number;
}

interface Coverage {
	tested: string[];
	pending: string[];
	blocked: string[];
}

interface RedTeamState {
	phase: Phase;
	target: string;
	startedAt: number;
	findings: Finding[];
	coverage: Coverage;
}

const PHASE_ENUM = Type.Union(PHASES.map((p) => Type.Literal(p)));

function freshState(target = ""): RedTeamState {
	return {
		phase: "orient",
		target,
		startedAt: Date.now(),
		findings: [],
		coverage: { tested: [], pending: [], blocked: [] },
	};
}

// Disk mirror of the state (research-doc rules: atomic writes, corruption
// raised loudly, never silently reset). Layout:
//   <dir>/phase.json     — phase/target/timestamps
//   <dir>/findings.json  — findings + coverage (THE cross-process contract)
// <dir> picks the FIRST of:
//   1. $SEPP_REDTEAM_LEDGER_DIR        (operator override)
//   2. the active session workspace   (set by /sepp via sepp.ts — one home per hunt)
//   3. data/sepp-redteam/              (fixed fallback)
function atomicWrite(path: string, data: string) {
	const tmp = `${path}.${process.pid}.tmp`;
	fs.writeFileSync(tmp, data, "utf8");
	fs.renameSync(tmp, path);
}

// ----------------------------------------------------------------------------
// Module state + persistence (custom session entries, sepp.ts pattern)
// ----------------------------------------------------------------------------

let state: RedTeamState = freshState();
let seq = 0;
function nextId(): string {
	seq += 1;
	return `SR-${Date.now().toString(36)}-${seq}`;
}

function restoreFromBranch(ctx: ExtensionContext) {
	const branch = ctx.sessionManager.getBranch?.() ?? ctx.sessionManager.getEntries?.() ?? [];
	let latest: RedTeamState | undefined;
	for (const entry of branch as any[]) {
		if (entry?.type === "custom" && entry?.customType === STATE_ENTRY) {
			latest = entry.data as RedTeamState;
		}
	}
	if (latest && typeof latest.phase === "string") {
		state = {
			...freshState(latest.target),
			...latest,
			coverage: latest.coverage ?? { tested: [], pending: [], blocked: [] },
			findings: latest.findings ?? [],
		};
		seq = state.findings.length;
	}
}

// ----------------------------------------------------------------------------
// Formatting helpers
// ----------------------------------------------------------------------------

function sevColor(theme: Theme, sev: Severity): string {
	const token = sev === "critical" ? "error" : sev === "high" ? "warning" : sev === "low" ? "dim" : "muted";
	return theme.fg(token, sev.toUpperCase());
}

function statusColor(theme: Theme, st: Status): string {
	return st === "proven" ? theme.fg("accent", "proven") : theme.fg(st === "hunch" ? "dim" : "muted", st);
}

function counts(findings: Finding[]): { bySev: Record<Severity, number>; byStatus: Record<Status, number> } {
	const bySev = { critical: 0, high: 0, medium: 0, low: 0 } as Record<Severity, number>;
	const byStatus = { proven: 0, probable: 0, hunch: 0 } as Record<Status, number>;
	for (const f of findings) {
		bySev[f.severity] += 1;
		byStatus[f.status] += 1;
	}
	return { bySev, byStatus };
}

function statusLines(s: RedTeamState, ledgerPath = ""): string[] {
	if (!s.target) return ["sepp-redteam: idle — arm with /hunt start <url>"];
	const c = counts(s.findings);
	const cov = `cov ${s.coverage.tested.length}✓ ${s.coverage.pending.length}… ${s.coverage.blocked.length}⊘`;
	return [
		`[${s.phase}] ${s.target} — ${s.findings.length} finding(s) (P:${c.byStatus.proven} Pr:${c.byStatus.probable} H:${c.byStatus.hunch}) ${cov}`,
		...(ledgerPath ? [`ledger: ${ledgerPath}`] : []),
	];
}

// Evidence sanity check: AGENTS.md says a finding is a request pair. Soft-warn
// (never block) when the evidence looks like pure narration instead of
// transactions — keep the model honest without becoming a bottleneck.
function evidenceWarning(evidence: string, requestId?: string): string | null {
	if (requestId && /^\d+$/.test(requestId.trim())) return null;
	if (/\b(req(uest)?\s*#?\s*\d+)\b/i.test(evidence)) return null;
	if (/\b(GET|POST|PUT|PATCH|DELETE|HTTP\/\d)\b/.test(evidence)) return null;
	return "evidence reads as narration — AGENTS.md wants request pairs: cite Caido request IDs (caido-cli request <id>) or paste the raw transaction";
}

// ----------------------------------------------------------------------------
// Extension
// ----------------------------------------------------------------------------

export default function sepRedteamExtension(pi: ExtensionAPI) {
	let currentCtx: ExtensionContext | undefined;

	// ---------------------------------------------------------- disk ledger
	const ENV_DIR = process.env.SEPP_REDTEAM_LEDGER_DIR ?? "";
	let ledgerPath = "";
	// The resolved directory is PINNED for the lifetime of one armed ledger.
	// Re-resolving per flush would silently split writes across two homes if
	// /sepp created a workspace mid-session (split-brain copies). Re-resolution
	// happens only at session start and when /hunt (re-)arms.
	let pinnedDir = "";

	function findWorkspace(): string {
		try {
			const sm = (currentCtx as any)?.sessionManager;
			const entries: any[] = sm?.getBranch?.() ?? sm?.getEntries?.() ?? [];
			for (let i = entries.length - 1; i >= 0; i--) {
				const e = entries[i];
				if (e?.type === "custom" && e?.customType === "sepp-target-state" && e?.data?.currentWorkspace) {
					return String(e.data.currentWorkspace);
				}
			}
		} catch {
			/* no session yet */
		}
		return "";
	}

	function ledgerDir(): string {
		if (!pinnedDir) pinnedDir = ENV_DIR || findWorkspace() || `${REPO}/data/sepp-redteam`;
		return pinnedDir;
	}

	function flushDisk() {
		if (!state.target) {
			ledgerPath = "";
			return;
		}
		try {
			const dir = ledgerDir();
			fs.mkdirSync(dir, { recursive: true });
			atomicWrite(
				`${dir}/phase.json`,
				JSON.stringify(
					{
						phase: state.phase,
						target: state.target,
						startedAt: new Date(state.startedAt).toISOString(),
						updatedAt: new Date().toISOString(),
					},
					null,
					2,
				) + "\n",
			);
			atomicWrite(
				`${dir}/findings.json`,
				JSON.stringify({ target: state.target, coverage: state.coverage, findings: state.findings }, null, 2) + "\n",
			);
			ledgerPath = dir;
		} catch (e) {
			// Loud, never fatal: session entries stay authoritative.
			console.error("[sepp-redteam] disk ledger flush FAILED:", e);
		}
	}

	// Hydrate from disk ONLY when the session carried no ledger of its own and a
	// definite directory exists (env override or known workspace). A corrupt
	// ledger is reported loudly — per the research-doc stance we do NOT silently
	// discard or reset it.
	function hydrateDisk(): boolean {
		if (state.target) return false;
		const dir = ENV_DIR || findWorkspace();
		if (!dir || !fs.existsSync(`${dir}/findings.json`)) return false;
		try {
			const parsed = JSON.parse(fs.readFileSync(`${dir}/findings.json`, "utf8"));
			if (!parsed?.target || !Array.isArray(parsed.findings)) throw new Error("unexpected shape");
			state = {
				...freshState(parsed.target),
				...parsed,
				findings: parsed.findings,
				coverage: parsed.coverage ?? { tested: [], pending: [], blocked: [] },
			};
			seq = state.findings.length;
			ledgerPath = dir;
			pinnedDir = dir; // keep writing where the ledger lives
			try {
				const pj = JSON.parse(fs.readFileSync(`${dir}/phase.json`, "utf8"));
				if (typeof pj?.phase === "string" && PHASES.includes(pj.phase as Phase)) state.phase = pj.phase as Phase;
			} catch {
				/* phase.json optional */
			}
			return true;
		} catch (e) {
			console.error("[sepp-redteam] EXISTING LEDGER UNREADABLE (left untouched):", e);
			void notify(`sepp-redteam: ledger in ${dir} is corrupt (${String(e)}) — NOT reset; session stays empty until you /hunt start. Inspect ${dir}/findings.json`, "warning");
			return false;
		}
	}

	function persist() {
		try {
			pi.appendEntry(STATE_ENTRY, state);
		} catch {
			/* session persistence best-effort */
		}
		flushDisk();
	}

	function renderWidget() {
		const ctx = currentCtx as any;
		if (!ctx?.ui?.setWidget) return;
		const lines = statusLines(state, ledgerPath);
		ctx.ui.setWidget(
			WIDGET_KEY,
			(_tui: unknown, theme: Theme) => {
				const box = new Box(1, 0);
				box.addChild(new Text(" ", 0, 0)); // spacer line (Text("") renders zero lines)
				for (const l of lines) {
					box.addChild(new Text(theme.fg("accent", "☠ sepp") + " " + l, 0, 0));
				}
				return box;
			},
			{ placement: "belowEditor" },
		);
	}

	const notify = async (msg: string, tone: "info" | "warning" = "info") => {
		const ctx = currentCtx as any;
		if (ctx?.ui?.notify) await ctx.ui.notify(msg, tone);
	};

	// -------------------------------------------------------------- lifecycle

	pi.on("session_start", async (_event, ctx) => {
		currentCtx = ctx;
		pinnedDir = ""; // fresh resolution at session start
		restoreFromBranch(ctx);
		if (hydrateDisk()) await notify(`sepp-redteam ledger restored from disk: ${ledgerPath}`, "info");
		renderWidget();
	});
	pi.on("session_tree", async (_event, ctx) => {
		currentCtx = ctx;
		pinnedDir = "";
		restoreFromBranch(ctx);
		if (hydrateDisk()) await notify(`sepp-redteam ledger restored from disk: ${ledgerPath}`, "info");
		renderWidget();
	});

	// ------------------------------------------------------------------- tools

	// Tool 1: the evidence-checked findings sink (forked from analyze_vulnerability,
	// de-confabulated: nothing is invented here, only what the caller supplies).
	pi.registerTool({
		name: "sepp_record_finding",
		label: "Record finding",
		description:
			"Record a SECURITY FINDING with its evidence. AGENTS.md rule: an issue only counts with a before/after request pair. " +
			"Cite the Caido request IDs (or paste the raw transaction) in `evidence`; use `status=hunch` if you merely suspect. " +
			"This tool stores exactly what you pass — it performs no analysis and no network I/O.",
		parameters: Type.Object({
			type: stringEnum([
				"bola",
				"jwt_oauth_flaw",
				"injection_sqli",
				"llm_job_abuse",
				"mass_assignment_idor",
				"info_leak",
				"access_control",
				"auth_weakness",
				"config_misconfiguration",
				"other",
			]),
			severity: Type.Optional(stringEnum(["critical", "high", "medium", "low"] as const)),
			status: stringEnum(["proven", "probable", "hunch"] as const, {
				description: "proven = request pair shows it; probable = strong indirect evidence; hunch = suspicion only",
			}),
			endpoint: Type.String({ description: "URL / endpoint affected" }),
			claim: Type.String({ description: "One sentence: what is broken" }),
			evidence: Type.String({ description: "Observed proof: status codes, response diff, request IDs. NOT narration like 'typically vulnerable'." }),
			requestId: Type.Optional(Type.String({ description: "Primary Caido request id anchoring the evidence" })),
			recommendation: Type.Optional(Type.String()),
			recordedBy: Type.Optional(Type.String({ description: '"controller" or "hunter:<name>"' })),
		}),

		async execute(_toolCallId, params) {
			const finding: Finding = {
				id: nextId(),
				type: params.type,
				severity: (params.severity ?? "medium") as Severity,
				status: params.status,
				endpoint: params.endpoint,
				claim: params.claim,
				evidence: params.evidence,
				requestId: params.requestId,
				recommendation: params.recommendation,
				recordedBy: params.recordedBy ?? "controller",
				timestamp: Date.now(),
			};
			state.findings.push(finding);
			state.phase = state.phase === "hypothesize" ? "prove" : state.phase;
			persist();
			renderWidget();

			const warn = evidenceWarning(finding.evidence, finding.requestId);
			const text = `recorded ${finding.id} [${finding.severity}/${finding.status}] ${finding.type} — ${finding.endpoint}` +
				(warn ? `\nNOTE: ${warn}` : "");
			return {
				content: [{ type: "text", text }],
				details: { finding, totalFindings: state.findings.length },
			};
		},

		renderCall(args: any, theme: Theme) {
			const sev: string = args?.severity ?? "medium";
			const color = sev === "critical" ? "error" : sev === "high" ? "warning" : "muted";
			const text =
				theme.fg("toolTitle", theme.bold("sepp_record_finding ")) +
				theme.fg("accent", String(args?.endpoint ?? "?")) +
				theme.fg(color, ` [${sev}/${args?.status ?? "?"}]`);
			return new Text(text, 0, 0);
		},

		renderResult(result: any, { expanded }: any, theme: Theme) {
			const f = result?.details?.finding as Finding | undefined;
			if (!f) return new Text("(no result)", 0, 0);
			if (!expanded) {
				return new Text(
					theme.fg("accent", f.id) +
						" " + sevColor(theme, f.severity) +
						" " + statusColor(theme, f.status) +
						` ${f.type} ${f.endpoint}`,
					0, 0,
				);
			}
			const box = new Box(1, 0);
			box.addChild(new Text(theme.fg("accent", theme.bold(`${f.id}  ${f.severity.toUpperCase()} · ${f.status}`)), 0, 0));
			box.addChild(new Text(`${f.type}  ${f.endpoint}`, 0, 0));
			box.addChild(new Text(theme.fg("muted", "claim: ") + f.claim, 0, 0));
			box.addChild(new Text(theme.fg("muted", "evidence: ") + f.evidence, 0, 0));
			if (f.requestId) box.addChild(new Text(theme.fg("muted", "request: ") + f.requestId, 0, 0));
			if (f.recommendation) box.addChild(new Text(theme.fg("muted", "fix: ") + f.recommendation, 0, 0));
			return box;
		},
	});

	// Tool 2: coordination (forked from redteam_coordinate: spawn/broadcast/gather
	// were never functional — dropped; phase + coverage kept, aligned to AGENTS.md loop).
	pi.registerTool({
		name: "sepp_coordinate",
		label: "Hunt coordination",
		description:
			"Track hunt phase and coverage. Phases: orient(map history/auth) → map(routes,params) → hypothesize(name weaknesses, cheap experiments) → prove(before/after pairs) → record(Caido ledger+report) → report(ranked list). " +
			"Pure bookkeeping — no I/O.",
		parameters: Type.Object({
			action: stringEnum(["set_phase", "coverage", "status", "finalize"] as const),
			phase: Type.Optional(PHASE_ENUM),
			addTested: Type.Optional(Type.Array(Type.String(), { description: "Areas conclusively covered" })),
			addPending: Type.Optional(Type.Array(Type.String())),
			addBlocked: Type.Optional(Type.Array(Type.String(), { description: "Areas blocked (approval/stale creds/etc.)" })),
			note: Type.Optional(Type.String()),
		}),

		async execute(_toolCallId, params) {
			switch (params.action) {
				case "set_phase": {
					if (!params.phase) break;
					state.phase = params.phase;
					break;
				}
				case "coverage": {
					if (params.addTested) pushUnique(state.coverage.tested, params.addTested);
					if (params.addPending) pushUnique(state.coverage.pending, params.addPending);
					if (params.addBlocked) pushUnique(state.coverage.blocked, params.addBlocked);
					break;
				}
				case "status":
					break;
				case "finalize": {
					state.phase = "report";
					break;
				}
			}
			persist();
			renderWidget();
			const c = counts(state.findings);
			return {
				content: [{
					type: "text",
					text:
						`phase=${state.phase} target=${state.target || "(unset)"}\n` +
						`findings=${state.findings.length} (proven=${c.byStatus.proven} probable=${c.byStatus.probable} hunch=${c.byStatus.hunch})\n` +
						`tested=${state.coverage.tested.length} pending=${state.coverage.pending.length} blocked=${state.coverage.blocked.length}` +
						(params.note ? `\nnote: ${params.note}` : ""),
				}],
				details: { state },
			};
		},

		renderCall(args: any, theme: Theme) {
			const extra = args?.phase ? ` → ${args.phase}` : args?.addTested ? ` +${args.addTested.length} tested` : "";
			return new Text(
				theme.fg("toolTitle", theme.bold("sepp_coordinate ")) + theme.fg("accent", String(args?.action ?? "?")) + theme.fg("dim", extra),
				0, 0,
			);
		},

		renderResult(result: any, { expanded }: any, theme: Theme) {
			const s = result?.details?.state as RedTeamState | undefined;
			if (!s) return new Text("(no result)", 0, 0);
			if (!expanded) return new Text(statusLines(s).join("\n"), 0, 0);
			const box = new Box(1, 0);
			box.addChild(new Text(theme.fg("accent", theme.bold(`hunt [${s.phase}] ${s.target}`)), 0, 0));
			const cov = (label: string, items: string[]) => {
				if (!items.length) return;
				box.addChild(new Text(theme.fg("muted", `${label}:`) + " " + items.join("; "), 0, 0));
			};
			cov("tested", s.coverage.tested);
			cov("pending", s.coverage.pending);
			cov("blocked", s.coverage.blocked);
			for (const f of s.findings.slice(-8)) {
				box.addChild(new Text(`${f.id} ${sevColor(theme, f.severity)} ${statusColor(theme, f.status)} ${f.endpoint}`, 0, 0));
			}
			if (s.findings.length > 8) box.addChild(new Text(`…and ${s.findings.length - 8} more`, 0, 0));
			return box;
		},
	});

	// --------------------------------------------------------------- command

	pi.registerCommand("hunt", {
		description: "sepp-redteam ledger: /hunt | start <url> | here <url> | phase <name> | report",
		handler: async (args, ctx) => {
			currentCtx = ctx;
			const parts = (args ?? "").trim().split(/\s+/).filter(Boolean); // FIX: upstream's "".split(" ") → [""] bug

			// Bare /hunt or /hunt status → show status in the command output (persistent),
			// not just a toast. FIX: upstream relied on ~2s toasts and had no else-branch.
			if (parts.length === 0 || parts[0] === "status") {
				await ctx.ui.notify(statusLines(state, ledgerPath).join("\n"), "info");
				renderWidget();
				return;
			}

			if (parts[0] === "start" || parts[0] === "here") {
				const here = parts[0] === "here";
				const rest = parts.slice(1).join(" ");
				const url = rest.match(/https?:\/\/[^\s,;"']+/)?.[0] ?? "";
				if (!url) {
					await ctx.ui.notify(`Usage: /hunt ${parts[0]} <url>`, "warning");
					return;
				}
				const notes = rest.replace(url, "").trim();
				state = freshState(url);
				pinnedDir = ""; // re-resolve: a workspace may exist by now
				persist();
				renderWidget();
				await ctx.ui.notify(
					here
						? `Full red team ARMED for ${url} (phase orient) — kickoff injected.`
						: `Ledger armed for ${url} — phase orient.\nThe hunt itself follows AGENTS.md (/sepp flow). Nothing is probed from this command.`,
					"info",
				);
				if (here) {
					// The honest rebirth of upstream /attack start: upstream LOCKED the
					// model onto two no-I/O ledger tools and banned everything else —
					// the direct road to confabulation (ISSUES rows 31–32). Here the
					// ledger is SECONDARY; probing happens with real tools under the
					// standing AGENTS.md rules (scope, rate budget, approval for
					// state-changing probes, evidence-or-it-did-not-happen).
					await pi.sendUserMessage(
						[
							`FULL RED TEAM ENGAGEMENT — ${url} — conducted BY YOU, IN THIS SESSION.`,
							notes ? `Operator context: ${notes}` : "",
							``,
							`This is the /hunt here mode: no hunter subagents, you do the hands-on work end-to-end.`,
							`Rules (still binding — none are relaxed by this mode):`,
							`- Scope: only ${url} (and hosts it provably serves from). Nothing else.`,
							`- Rate budget: ≤50 requests per burst, ≥1s pause between batches; STOP on signs of degradation.`,
							`- Read-only probes by default. ANY state-changing probe (DELETE, role changes, writes with forged IDs, job floods) requires the OPERATOR's explicit go-ahead in chat first.`,
							`- Secrets hygiene: reference Caido request IDs; never print full tokens.`,
							``,
							`Method: orient (Caido history via caido-cli) → map → hypothesize (name the weakness + the observable difference) → prove (before/after request PAIRS via caido-cli send/raw/batch) → record.`,
							``,
							`Bookkeeping while you work:`,
							`- sepp_coordinate — keep phase + coverage (tested/pending/blocked) current.`,
							`- sepp_record_finding — record EVERY finding with status proven/probable/hunch; "proven" DEMANDS the Caido request IDs of a before/after pair. A finding without cited transactions is a hypothesis, and hypotheses are NOT findings (AGENTS.md rule 5).`,
							`- Finish with /hunt report and the ranked table (finding | impact one-liner | PoC request IDs | confidence).`,
						].filter(Boolean).join("\n"),
					);
				}
				return;
			}

			if (parts[0] === "phase") {
				const p = parts[1] as Phase | undefined;
				if (!p || !PHASES.includes(p)) {
					await ctx.ui.notify(`Unknown phase. Valid: ${PHASES.join(" ")}`, "warning");
					return;
				}
				state.phase = p;
				persist();
				renderWidget();
				await ctx.ui.notify(`Phase → ${p}`, "info");
				return;
			}

			if (parts[0] === "report") {
				if (state.findings.length === 0) {
					await ctx.ui.notify("No findings recorded yet", "warning");
					return;
				}
				const path = writeReport();
				await ctx.ui.notify(`Report written: ${path}`, "info");
				return;
			}

			await ctx.ui.notify("Usage: /hunt [status | start <url> | phase <name> | report]", "warning");
		},
	});

	function writeReport(): string {
		const d = new Date();
		const stamp = [
			d.getFullYear(),
			String(d.getMonth() + 1).padStart(2, "0"),
			String(d.getDate()).padStart(2, "0"),
		].join("-") + "-" + [d.getHours(), d.getMinutes(), d.getSeconds()].map((n) => String(n).padStart(2, "0")).join("");
		const rank: Record<Severity, number> = { critical: 0, high: 1, medium: 2, low: 3 };
		const sorted = [...state.findings].sort((a, b) => rank[a.severity] - rank[b.severity]);

		const md: string[] = [
			`# SEPP redteam ledger report — ${stamp}`,
			`Target: ${state.target || "(unset)"}`,
			`Phase: ${state.phase}`,
			...(ledgerPath ? [`Ledger: ${ledgerPath}`] : []),
			"",
			"| # | id | severity | status | type | endpoint | claim | request |",
			"|---|----|----------|--------|------|----------|-------|---------|",
			...sorted.map((f, i) =>
				`| ${i + 1} | ${f.id} | ${f.severity} | ${f.status} | ${f.type} | ${f.endpoint} | ${f.claim.replaceAll("|", "\\|")} | ${f.requestId ?? "-"} |`),
			"",
			...(state.coverage.tested.length ? [`## Tested`, ...state.coverage.tested.map((t) => `- ${t}`), ""] : []),
			...(state.coverage.pending.length ? [`## Pending`, ...state.coverage.pending.map((t) => `- ${t}`), ""] : []),
			...(state.coverage.blocked.length ? [`## Blocked`, ...state.coverage.blocked.map((t) => `- ${t}`), ""] : []),
			`## Evidence`,
			...sorted.map((f) =>
				[
					`### ${f.id} — ${f.claim} (${f.severity}/${f.status})`,
					f.evidence,
					f.recommendation ? `_Fix: ${f.recommendation}_` : "",
					f.recordedBy ? `_Recorded by: ${f.recordedBy}_` : "",
				].filter(Boolean).join("\n"),
			),
		];

		const dir = `${REPO}/data/reports`;
		fs.mkdirSync(dir, { recursive: true });
		const path = `${dir}/sepp-redteam-${stamp}.md`;
		fs.writeFileSync(path, md.join("\n") + "\n", "utf8");
		return path;
	}

	function pushUnique(list: string[], items: string[]) {
		for (const it of items) if (!list.includes(it)) list.push(it);
	}
}
