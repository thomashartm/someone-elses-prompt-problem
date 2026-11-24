/**
 * /sepp <url> [info...] — kick off a security hunt against a target.
 *
 * Project-local pi extension (auto-discovered from .pi/extensions/).
 *
 * Flow:
 *   1. Parses the first URL-looking token as the target; the rest is context.
 *   2. Preflight (no target traffic): Caido reachable + token freshness gate
 *      (scripts/harvest_target.py, exit 2 = stale -> asks to continue).
 *   3. Composes a kickoff prompt per AGENTS.md and sends it as a user message
 *      (pi.sendUserMessage) so the agent starts the hunt immediately.
 *
 * Convenience commands:
 *   /sepp-refresh  — re-harvest freshest cookie from Caido history, report age.
 */
import type { ExtensionAPI, ExtensionContext } from "@earendil-works/pi-coding-agent";
import { Box, Text } from "@earendil-works/pi-tui";
import * as fs from "node:fs";
import { showReader } from "../lib/reader.ts";
import { requireCaido } from "../lib/caido.ts";

const REPO = process.cwd(); // pi runs from the repo root (sepp.sh cds there)
const SCRIPTS = `${REPO}/scripts`;
const WIDGET_KEY = "sepp-target";
const HUNTERS_WIDGET_KEY = "sepp-hunters";

interface ExecOut {
	stdout: string;
	stderr: string;
	code: number | undefined;
}

async function run(pi: import("@earendil-works/pi-coding-agent").ExtensionAPI, cmd: string, args: string[]): Promise<ExecOut> {
	try {
		const res = await pi.exec(cmd, args, { timeout: 60_000 });
		return { stdout: res.stdout, stderr: res.stderr, code: res.code ?? 0 };
	} catch (e) {
		return { stdout: "", stderr: String(e), code: 127 };
	}
}

function firstUrl(text: string): string | undefined {
	const m = text.match(/https?:\/\/[^\s,;"']+/);
	return m?.[0];
}

export default function huntExtension(pi: ExtensionAPI) {
	const preflight = async (ctx: any): Promise<{ ok: boolean; tokenNote: string }> => {
		if (!await requireCaido(pi, ctx)) return { ok: false, tokenNote: "Caido unavailable" };

		// 2. Token freshness gate
		const hv = await run(pi, "python3", [`${SCRIPTS}/harvest_target.py`]);
		let tokenNote = hv.stdout.trim() || hv.stderr.trim();
		if (hv.code === 2) {
			await ctx.ui.notify("Token stale: " + tokenNote, "warning");
			const cont = await ctx.ui.confirm("Stale token", "Continue without fresh credentials?");
			if (!cont) return { ok: false, tokenNote: tokenNote + " (aborted by operator)" };
			tokenNote += " — OPERATOR ACCEPTED STALE TOKEN";
		} else if (hv.code !== 0) {
			await ctx.ui.notify("Harvest problem: " + tokenNote, "warning");
		} else {
			await ctx.ui.notify(tokenNote || "Token fresh", "info");
		}
		return { ok: true, tokenNote };
	};

	const sepStartCmd = {
		description: "Start a hunt session: /hunt-start <url> [context...] — pi plans, background hunters execute legs",
		handler: async (args: string, ctx: ExtensionContext) => {
			const argText = args.trim();
			if (!argText) {
				ctx.ui.notify("Usage: /sepp <url> [info...]", "warning");
				return;
			}
			const url = firstUrl(argText) ?? "";
			const info = argText.replace(url, "").trim();

			const pf = await preflight(ctx);
			if (!pf.ok) return;

			// --- session workspace (one per host per day, targets accumulate)
			let wsLine = "Workspace: (none — host unknown)";
			let wsPath = "";
			if (url) {
				let host = "";
				try { host = new URL(url).hostname; } catch { host = ""; }
				const ws = await run(pi, "python3", [
					`${SCRIPTS}/workspace.py`, "ensure",
					"--host", host || "unknown-host",
					"--url", url,
					"--context", info,
					"--cred-pointer", `data/targets/${process.env.CAIDO_TARGET ?? "testbed"}/target.json`,
				]);
				try {
					const wsInfo = JSON.parse(ws.stdout.trim());
					wsPath = wsInfo.path;
					wsLine = `Workspace: ${wsInfo.path}${wsInfo.created ? " (new)" : " (resumed, targets: " + wsInfo.targets + ")"}`;
				} catch {
					await ctx.ui.notify("workspace.py failed: " + (ws.stderr || ws.stdout), "warning");
				}
			}

			const prompt = [
				"SEPP ASSIGNMENT",
				url ? `Target: ${url}` : "Target: (see info — derive endpoints from Caido history)",
				info ? `Operator context: ${info}` : "",
				`Credentials: ${pf.tokenNote}`,
				wsLine,
				"",
				"Operate as the CONTROLLER exactly per AGENTS.md in this repo: you orient, plan, assign, integrate and report — the hands-on hunt runs in a `hunter` SUBAGENT (pi-subagents). Reminder of the essentials:",
				"- Split: controller = strategy/operator/report; hunter subagent = orient/map/probe/prove inside the workspace. Launch via the subagent tool, agent `hunter`, with the target, credentials pointer, workspace path and this assignment summarized as the task. BACKGROUND it (async) so you stay available; collect with bg_wait.",
				"- Controller may do ZERO-target-traffic orientation itself (reading Caido history via caido-cli/MCP is fine). Anything that touches the target goes through a hunter subagent.",
				"- NEW TARGET (no adapter yet): hunter captures baseline + writes data/targets/<slug>/adapter.py per drivers/ADAPTER_CONTRACT.md during orientation, verified by scripts/check_adapters.py — then garak/SEPP shim work with zero code changes.",
				"- Read-only probes only. ANY state-changing probe requires MY (controller's) explicit approval first — the hunter escalates to the operator via the supervisor channel; I relay the operator's answer.",
				"- ContentFilterError permanently burns a thread (proven defect): always use conv=False (fresh thread per request) — the SEPP shim does this by policy; see drivers/ADAPTER_CONTRACT.md.",
				"- SESSION WORKSPACE: all scripts, tools, evidence and reports the hunter produces MUST live inside the workspace given above",
				"  (scripts/ for executable helpers + its README.md index, tools/, evidence/ for captures/diffs, reports/ for the report).",
				"- Record: findings into Caido via helpers/findings.py (ledger at data/reports/findings.json) + <workspace>/reports/YYYY-MM-DD-<host>.md — the hunter records, you verify.",
				"- Tooling troubles go into GitHub Issues via `gh issue create` (rule 7).",
				"- Close with the ranked report table (claim/impact/PoC/status) and confidence levels. Begin with orientation; launch the first hunter leg as soon as the workspace + credentials pointer are confirmed.",
			].filter(Boolean).join("\n");

			await ctx.ui.notify("SEPP briefed — controller taking over", "info");
			if (url) setTarget(url, ctx);
			if (wsPath) setWorkspace(wsPath, ctx);
			await pi.sendUserMessage(prompt);
		},
	};
	pi.registerCommand("hunt-start", sepStartCmd);
	pi.registerCommand("sepp", { ...sepStartCmd, description: `Alias of /hunt-start` });

	// ---------------------------------------------------------------- target widget
	// Panel below the editor showing the current target URL + session workspace.
	// Set explicitly by /sepp, or inferred the first time the agent touches a URL.
	// Persists per session via a custom entry.
	let currentTarget = "";
	let lastSeen = "";
	let currentWorkspace = "";

	const URL_RE = /https?:\/\/[^\s"'`<>{}|\\^\[\]]+/g;

	function extractUrls(text: string): string[] {
		return (text.match(URL_RE) ?? []).map((u) => u.replace(/[.,;:)]+$/, ""));
	}

	function renderWidget(ctx: any) {
		if (!currentTarget && !currentWorkspace) {
			ctx.ui.setWidget(WIDGET_KEY, undefined, { placement: "belowEditor" });
			return;
		}
		// Component factory (backlog Issue 1, done): themed Box + Text.
		const lines: string[] = [];
		if (currentTarget) {
			lines.push(`${"⌖ target"}  ${currentTarget}`);
			if (lastSeen && lastSeen !== currentTarget) lines.push(`↳ recent  ${lastSeen}`);
		}
		if (currentWorkspace) {
			// short form: .../workspaces/<id>
			const parts = currentWorkspace.split("/");
			lines.push(`▤ session  ${parts.slice(-2).join("/")}`);
		}
		ctx.ui.setWidget(
			WIDGET_KEY,
			(_tui: unknown, theme: import("@earendil-works/pi-coding-agent").Theme) => {
				const box = new Box(1, 0);
				// breathing room: pi mounts belowEditor widgets flush under the
				// editor's bottom border - a single-space line separates them
				// (Text("") renders ZERO lines, hence the space)
				box.addChild(new Text(" ", 0, 0));
				lines.forEach((l, i) => {
					const colored = i === 0 && currentTarget
						? theme.fg("accent", "⌖ target") + " " + theme.bold(currentTarget)
						: i === 0 && currentWorkspace
							? theme.fg("accent", "▤ session") + " " + currentWorkspace.split("/").slice(-2).join("/")
							: l.startsWith("↳")
								? theme.fg("muted", "↳ recent") + " " + l.replace(/^↳\s*recent\s*/, "")
								: "▤ session" + " " + currentWorkspace.split("/").slice(-2).join("/");
					box.addChild(new Text(colored, 0, 0));
				});
				return box;
			},
			{ placement: "belowEditor" },
		);
	}

	function setTarget(url: string, ctx: any) {
		currentTarget = url;
		renderWidget(ctx);
		persistState();
	}

	function setWorkspace(path: string, ctx: any) {
		currentWorkspace = path;
		renderWidget(ctx);
		persistState();
	}

	function persistState() {
		try {
			pi.appendEntry("sepp-target-state", { currentTarget, lastSeen, currentWorkspace });
		} catch {
			/* persistence is best-effort */
		}
	}

	// Track URLs the agent works with; adopt the first as target if unset.
	pi.on("tool_call", async (event, ctx) => {
		if (event.type !== "tool_call") return;
		const haystack =
			typeof (event as any).input === "object" && (event as any).input !== null
				? JSON.stringify((event as any).input)
				: String((event as any).input ?? "");
		const urls = extractUrls(haystack);
		if (urls.length === 0) return;
		const newest = urls[urls.length - 1];
		if (newest !== lastSeen) {
			lastSeen = newest;
			if (!currentTarget) currentTarget = urls[0];
			renderWidget(ctx);
		}
	});

	pi.on("input", async (event, ctx) => {
		if (/^\s*(?:target\s*:|https?:\/\/)/i.test(event.text) && !await requireCaido(pi, ctx)) {
			return { action: "handled" };
		}
	});

	// Also catch targets stated in plain prompts (before_agent_start sees all).
	pi.on("before_agent_start", async (event, ctx) => {
		const anyEv = event as any;
		const promptText: string = anyEv.prompt ?? "";
		const urls = extractUrls(promptText);
		if (urls.length && !currentTarget) setTarget(urls[0], ctx);
		else if (urls.length && urls[0] !== currentTarget) setTarget(urls[0], ctx);
	});

	// ---------------------------------------------------------- caido spool intake
	// Right-click dispatch in Caido (dispatch plugin, custom tool "Send to Hunt")
	// runs scripts/send_to_sep.sh which atomically drops <id>.json + <id>.raw
	// into data/spool/. We watch the dir, consume drops, and inject them into
	// the live session via pi.sendUserMessage (queued while the agent is busy).
	const SPOOL_DIR = `${REPO}/data/spool`;
	type SpoolDrop = {
		url: string;
		method?: string;
		raw?: string;
		has_raw?: boolean;
		id?: string;
	};
	let spoolTimer: ReturnType<typeof setTimeout> | undefined;
	let spoolCtx: any;
	let spoolWatchStarted = false;

	function readSpool(): SpoolDrop[] {
		const drops: SpoolDrop[] = [];
		try {
			for (const f of fs.readdirSync(SPOOL_DIR).sort()) {
				if (!f.endsWith(".json") || f.startsWith(".")) continue;
				const jp = `${SPOOL_DIR}/${f}`;
				const rp = jp.replace(/\.json$/, ".raw");
				try {
					const meta = JSON.parse(fs.readFileSync(jp, "utf8")) as SpoolDrop;
					meta.raw = fs.existsSync(rp) ? fs.readFileSync(rp, "utf8") : "";
					drops.push(meta);
					fs.unlinkSync(jp); // consume: exactly-once delivery
					if (fs.existsSync(rp)) fs.unlinkSync(rp);
				} catch {
					/* half-written or unreadable drop: leave for next tick */
				}
			}
		} catch {
			/* spool dir missing */
		}
		return drops;
	}

	function deliverDrops(drops: SpoolDrop[]) {
		if (!drops.length || !spoolCtx) return;
		setTarget(drops[drops.length - 1].url, spoolCtx);
		const blocks = drops.map((d) => {
			const head = `SEPP DECLARED FROM CAIDO (operator right-click, spool ${d.id ?? "?"})\nURL: ${d.url}\nMethod: ${d.method ?? "GET"}`;
			return d.raw ? `${head}\n\n\`\`\`http\n${d.raw.trimEnd()}\n\`\`\`` : head;
		});
		const noun = drops.length === 1 ? "this request" : "these requests";
		const msg =
			blocks.join("\n\n---\n\n") +
			`\n\nThe operator declared ${noun} Somebody Else's Problem — it is now yours. Fold ${drops.length === 1 ? "it" : "them"} into the current hunt: read-only orientation first (history, fit into the existing plan/findings). This does NOT authorize state-changing traffic — those still need explicit operator approval per AGENTS.md.`;
		void pi.sendUserMessage(msg);
		spoolCtx.ui.notify(`SEPP declared: ${drops.length === 1 ? drops[0].url : drops.length + " requests"}`, "info");
	}

	async function drainReadySpool() {
		if (!spoolCtx || !fs.readdirSync(SPOOL_DIR).some(f => !f.startsWith(".") && f.endsWith(".json"))) return;
		if (await requireCaido(pi, spoolCtx)) deliverDrops(readSpool());
	}

	function drainSpoolSoon() {
		if (spoolTimer) clearTimeout(spoolTimer);
		spoolTimer = setTimeout(() => {
			spoolTimer = undefined;
			void drainReadySpool().catch(e => console.error("[hunt] spool unavailable:", e));
		}, 300); // debounce: coalesce multi-select bursts
	}

	function startSpoolWatcher(ctx: any) {
		spoolCtx = ctx;
		if (spoolWatchStarted) return;
		try {
			fs.mkdirSync(SPOOL_DIR, { recursive: true });
			fs.watch(SPOOL_DIR, drainSpoolSoon);
			spoolWatchStarted = true;
			drainSpoolSoon();
		} catch (e) {
			console.error("[hunt] spool watcher failed:", e);
		}
	}

	// Restore state when a session loads (best-effort).
	pi.on("session_start", async (_event, ctx) => {
		try {
			const entries: any[] = ctx.sessionManager.getEntries?.() ?? [];
			for (let i = entries.length - 1; i >= 0; i--) {
				const e = entries[i];
				if (e?.type === "custom" && e?.customType === "sepp-target-state") {
					currentTarget = e.data?.currentTarget ?? "";
					lastSeen = e.data?.lastSeen ?? "";
					currentWorkspace = e.data?.currentWorkspace ?? "";
					break;
				}
			}
		} catch {
			/* ignore */
		}
		renderWidget(ctx);
		startSpoolWatcher(ctx);
		startHuntersPoller(ctx);
	});

	// ------------------------------------------------------- coordinator brief
	// /sepp-brief — the INTERPRETED engagement brief. Deterministic aggregation
	// (engagement_status.py) supplies the FACTS; the session agent — acting as
	// the sole coordinator — writes the interpretation (honest conclusion,
	// decisions, next steps) to data/reports/sepp-brief-latest.md.
	//   /sepp-brief        → brief the coordinator (injects the task)
	//   /sepp-brief show   → display the latest interpreted brief in the reader
	const sepBriefCmd = {
		description: "Coordinator interprets the engagement: /sepp-brief (run) | /sepp-brief show",
		handler: async (args: string, ctx: ExtensionContext) => {
			const parts = (args ?? "").trim().split(/\s+/).filter(Boolean);
			const briefPath = `${REPO}/data/reports/sepp-brief-latest.md`;

			if (parts[0] === "show") {
				try {
					const body = fs.readFileSync(briefPath, "utf8");
					await showReader(ctx, "SEPP engagement brief (coordinator-interpreted)", body);
				} catch {
					await ctx.ui.notify("No interpreted brief yet — run /sepp-brief first", "warning");
				}
				return;
			}

			const facts = await run(pi, "python3", [`${SCRIPTS}/engagement_status.py`]);
			const factsMd = (facts.stdout || "").trim() || "(aggregator produced nothing — consult data/hunters/ and helpers/findings.py directly)";
			const prompt = [
				"COORDINATOR BRIEF REQUEST — you are the SOLE SEPP coordinator for this engagement.",
				"Interpret the hunt results and write the engagement brief. Do the interpretation YOURSELF — that is the task; do not just reformat the facts.",
				"",
				"STEP 1 — Read for context (tools are yours):",
				"- the FACTS block below (deterministic: board + ledger + scan of the latest workspace report)",
				"- the full report(s): data/workspaces/*/reports/*.md of this engagement (the FACTS reference them)",
				"",
				"STEP 2 — WRITE data/reports/sepp-brief-latest.md with EXACTLY these sections:",
				"# SEPP engagement brief — <target> — <date>",
				"## Where the hunt stands",
				"(2-4 lines: legs closed, live hunters, findings — may reuse FACTS verbatim)",
				"## Attack-surface picture",
				"(copy the FACTS table UNCHANGED — rows are ledger-anchored, you do not edit evidence)",
				"## Honest conclusion",
				"(YOUR interpretation: what the proven findings MEAN together, what stays open, what the positive controls imply for the remaining attack surface; every non-obvious claim cites a Caido request id or workspace file; speculation allowed only if labeled hunch)",
				"## Decisions needed",
				"(numbered; each: the decision, your recommendation, what it costs/blockers; mark items needing operator approval or a second principal)",
				"## Next steps",
				"(numbered, concrete; drawn from the reports' proposed probes; tag each [read-only] or [needs approval])",
				"",
				"HARD RULES: never invent a finding or a number; hunches stay labeled; no full tokens; if the reports disagree with the FACTS, say so explicitly under Honest conclusion.",
				"STEP 3 — Reply with exactly one line: `brief written: data/reports/sepp-brief-latest.md`.",
				"",
				"FACTS (deterministic aggregate — scripts/engagement_status.py):",
				"~~~markdown",
				factsMd,
				"~~~",
			].join("\n");
			await pi.sendUserMessage(prompt);
			await ctx.ui.notify("Coordinator briefed — interpretation lands in data/reports/sepp-brief-latest.md (view with /sepp-brief show)", "info");
		},
	};
	pi.registerCommand("hunt-brief", sepBriefCmd);
	pi.registerCommand("sepp-brief", { ...sepBriefCmd, description: `Alias of /hunt-brief` });

	// ------------------------------------------------------------ hunter board
	// Live roster of ALL hunters (whatever launched them): each hunter
	// heartbeats data/hunters/<name>.json via scripts/hunter_board.py; we poll
	// that directory and render the roster under the target panel. Drill into
	// one hunter with /hunters (target, phase, last activity, workspace,
	// evidence, report tail).
	const HUNTERS_DIR = `${REPO}/data/hunters`;
	type HunterRow = {
		name: string; state: string; target?: string; phase?: string;
		activity?: string; workspace?: string; ageMin?: number; result?: string;
	};
	const PHASE_COLORS = {
		orient: "muted", map: "muted", hypothesize: "muted", prove: "warning",
		record: "accent", report: "accent", "wait-operator": "error",
	} as const;

	function readHunterRows(): HunterRow[] {
		const rows: HunterRow[] = [];
		const nowS = Math.floor(Date.now() / 1000);
		try {
			for (const f of fs.readdirSync(HUNTERS_DIR).sort()) {
				if (!f.endsWith(".json")) continue;
				try {
					const r = JSON.parse(fs.readFileSync(`${HUNTERS_DIR}/${f}`, "utf8"));
					const ageMin = Math.round((nowS - (r.updatedAtEpoch ?? 0)) / 6) / 10;
					rows.push({
						...r,
						ageMin,
						state: r.done ? "done" : ageMin > 10 ? "quiet" : "active", // derive — files store done+epoch, not state
					});
				} catch { /* torn read */ }
			}
		} catch { /* no board yet */ }
		const order: Record<string, number> = { active: 0, quiet: 1, done: 2 };
		rows.sort((a, b) => (order[a.state] ?? 9) - (order[b.state] ?? 9) || a.name.localeCompare(b.name));
		return rows;
	}

	function renderHuntersWidget(ctx: any) {
		const rows = readHunterRows();
		const live = rows.filter((r) => r.state !== "done");
		if (live.length === 0) {
			ctx.ui.setWidget(HUNTERS_WIDGET_KEY, undefined, { placement: "belowEditor" });
			return;
		}
		ctx.ui.setWidget(
			HUNTERS_WIDGET_KEY,
			(_tui: unknown, theme: import("@earendil-works/pi-coding-agent").Theme) => {
				const box = new Box(1, 0);
				box.addChild(new Text(theme.fg("muted", " "), 0, 0));
				box.addChild(new Text(theme.fg("accent", theme.bold("hunters")) + `  ${live.length} live`, 0, 0));
				for (const r of live) {
					const dot = r.state === "active" ? theme.fg("accent", "●") : r.state === "stopping" ? theme.fg("warning", "⏹") : theme.fg("warning", "○");
					const phase = theme.fg(PHASE_COLORS[(r.phase ?? "") as keyof typeof PHASE_COLORS] ?? "muted", r.phase ?? "?");
					box.addChild(new Text(`${dot} ${theme.bold(r.name)}  ${phase}  ${(r.activity ?? "").slice(0, 58)}`, 0, 0));
				}
				return box;
			},
			{ placement: "belowEditor" },
		);
	}

	let huntersTimer: ReturnType<typeof setInterval> | undefined;
	function startHuntersPoller(ctx: any) {
		if (huntersTimer) return;
		renderHuntersWidget(ctx);
		huntersTimer = setInterval(() => {
			try { renderHuntersWidget(ctx); } catch { /* session may be gone */ }
		}, 5_000);
	}

	pi.registerCommand("hunters", {
		description: "SEPP hunter board: all hunters, drill into one for details",
		handler: async (_args: string, ctx: ExtensionContext) => {
			const rows = readHunterRows();
			if (rows.length === 0) {
				await ctx.ui.notify("Board empty — no hunter has heartbeated yet (hunters run scripts/hunter_board.py heartbeat)", "info");
				return;
			}
			const lines = rows.map((r) => {
				const marker = r.state === "stopping" ? "⏹" : r.state === "active" ? "●" : r.state === "quiet" ? "○" : "✓";
				return `${marker} ${r.name}  [${r.state}]  ${r.phase ?? "?"}  ${(r.activity ?? "").slice(0, 50)}  (${r.ageMin ?? "?"}m)`;
			});
			const picked = await ctx.ui.select("Hunters · Esc closes", ["CLOSE", ...lines]);
			if (!picked || picked === "CLOSE") return;
			const name = (picked.match(/^\S+ (\S+)/) ?? [])[1];
			const det = await run(pi, "python3", [`${SCRIPTS}/hunter_board.py`, "get", name]);
			let detail = det.stdout || det.stderr;
			try {
				const row = JSON.parse(det.stdout);
				// enrich: newest evidence files + report tail from its workspace
				if (row.workspace) {
					const fsMod = await import("node:fs/promises");
					const evd = await fsMod.readdir(`${row.workspace}/evidence`).catch(() => [] as string[]);
					const newest = evd.filter((f) => !f.startsWith(".")).slice(-3);
					detail += `\nevidence (latest): ${newest.join(", ") || "(none yet)"}`;
					const repDir = `${row.workspace}/reports`;
					const reps = ((await fsMod.readdir(repDir).catch(() => [] as string[])) || []).filter((f) => f.endsWith(".md")).sort();
					if (reps.length) {
						const tail = (await fsMod.readFile(`${repDir}/${reps[reps.length - 1]}`, "utf8")).slice(-700);
						detail += `\n--- report tail (${reps[reps.length - 1]}) ---\n${tail}`;
					}
				}
			} catch { /* detail falls back to raw get output */ }
			await showReader(ctx, `Hunter ${name}`, detail);
		},
	});
	const sepStatusCmd = {
		description: "Engagement brief: where the hunt stands, attack-surface table, decisions & next steps",
		handler: async (args: string, ctx: ExtensionContext) => {
			const scope = (args ?? "").trim(); // optional workspace-id substring
			const scriptArgs = scope ? [scope] : [];
			const res = await run(pi, "python3", [`${SCRIPTS}/engagement_status.py`, ...scriptArgs]);
			const brief = (res.stdout || "").trim();
			if (res.code !== 0 || !brief) {
				await ctx.ui.notify(`status failed (exit ${res.code}): ${(res.stderr || "no output").slice(0, 400)}`, "warning");
				return;
			}
			// persist full brief, show in the bright reader (full text, scrollable)
			const stamp = new Date().toISOString().replace(/[:T]/g, "-").slice(0, 19);
			const path = `${REPO}/data/reports/sepp-status-${stamp}.md`;
			try { fs.mkdirSync(`${REPO}/data/reports`, { recursive: true }); fs.writeFileSync(path, brief + "\n", "utf8"); } catch { /* best effort */ }
			let interpNote = "";
			try {
				const bp = `${REPO}/data/reports/sepp-brief-latest.md`;
				const m = fs.statSync(bp).mtimeMs;
				interpNote = `\n\n(interpreted coordinator brief exists — written ${new Date(m).toLocaleString()}; view: /sepp-brief show · regenerate: /sepp-brief)`;
			} catch { /* none yet */ }
			await showReader(ctx, "SEPP status — deterministic facts", brief + `\n\n(facts saved: ${path})` + interpNote);
		},
	};
	pi.registerCommand("hunt-status", sepStatusCmd);
	pi.registerCommand("sepp-status", { ...sepStatusCmd, description: `Alias of /hunt-status` });

	const sepAbortCmd = {
		description: "Stop hunters: /hunt-abort <name> (one, cooperative) · /hunt-abort all (kill sweep) · bare = picker",
		handler: async (args: string, ctx: ExtensionContext) => {
			const aargs = (args ?? "").trim().split(/\s+/).filter(Boolean);
			let target = aargs[0];

			// no argument: let the operator CHOOSE — single hunter, all, or nothing.
			// (mass kill as the silent default was an accident waiting to happen)
			if (!target) {
				const live = readHunterRows().filter((r) => r.state !== "done");
				if (live.length === 0) {
					await showReader(ctx, "hunt-abort", "No live hunters to stop.\n\n(board: data/hunters/ — rows appear when a hunter heartbeats)");
					return;
				}
				const options = [
					...live.map((r) => `${r.name}  [${r.state}] ${r.phase ?? "?"} — ${(r.activity ?? r.result ?? "").slice(0, 48)}`),
					`ALL live hunters (${live.length})`,
					"CANCEL",
				];
				const picked = await ctx.ui.select("Stop which hunter?", options);
				if (!picked || picked === "CANCEL") return;
				target = picked.startsWith("ALL ") ? "all" : (picked.match(/^(\S+)/) ?? [])[1];
				if (target === "all") target = ""; // CLI: bare abort = all
			}

			// single hunter → cooperative stop (subagent-safe: pi hunters run
			// behind throwaway bash shells; SIGTERM can't reach them, but they
			// check their board row before every probe batch). Explicit ALL →
			// kill sweep + stop-request belt-and-braces.
			const runArgs = target && target !== "all" ? ["stop", target] : ["abort"];
			const res = await run(pi, "python3", [`${SCRIPTS}/hunter_board.py`, ...runArgs]);
			const out = (res.stdout + (res.stderr ? `\n${res.stderr}` : "")).trim();
			await showReader(
				ctx,
				`hunt-abort ${target || "(all)"}`,
				out || `abort exit ${res.code}`,
			);
			renderHuntersWidget(ctx);
		},
	};
	pi.registerCommand("hunt-abort", sepAbortCmd);
	pi.registerCommand("sepp-abort", { ...sepAbortCmd, description: `Alias of /hunt-abort` });

	// ------------------------------------------------------------ workspace cmds
	const sepsCmd = {
		description: "Hunt workspaces: list, then drill into one (worked-on files, where hunters stopped)",
		handler: async (_args: string, ctx: ExtensionContext) => {
			const res = await run(pi, "python3", [`${SCRIPTS}/workspace.py`, "list"]);
			try {
				const items = JSON.parse(res.stdout) as Array<Record<string, unknown>>;
				if (items.length === 0) {
					await ctx.ui.notify("No hunt workspaces yet", "info");
					return;
				}
				const lines = items.map((w) => {
					const targets = (w.targets as string[]) ?? [];
					const t0 = targets[0] ?? "?";
					const more = targets.length > 1 ? ` (+${targets.length - 1})` : "";
					return `${w.updated_at || "?"}  [${w.status}]  ${w.id}  ${t0}${more}  (${w.size_kb} KB)`;
				});
				const picked = await ctx.ui.select("Sessions · Esc closes", ["CLOSE", ...lines]);
				if (!picked || picked === "CLOSE") return;
				const idx = lines.indexOf(picked);
				const ws = items[idx >= 0 ? idx : 0];
				const wsPath = String(ws.path ?? "");

				// ---- assemble the drill-down detail
				const fsMod = await import("node:fs/promises");
				const parts: string[] = [
					`WORKSPACE ${ws.id}`,
					`path: ${wsPath}`,
					`targets: ${((ws.targets as string[]) ?? []).join(", ") || "?"}`,
					`last activity: ${ws.updated_at ?? "?"}   status: ${ws.status ?? "?"}`,
				];

				// what they worked on: per-dir file counts + newest artifacts
				const subdirs = ["scripts", "tools", "evidence", "reports"];
				for (const d of subdirs) {
					try {
						const ents = (await fsMod.readdir(`${wsPath}/${d}`)).filter((f) => !f.startsWith("."));
						parts.push(`${d}/ (${ents.length}): ${ents.slice(-4).join(", ") || "(empty)"}`);
					} catch { /* dir absent */ }
				}

				// where they stopped: board rows bound to this workspace
				const rows = readHunterRows();
				const mine = rows.filter((r) => (r.workspace ?? "").endsWith(wsPath) || wsPath.endsWith(r.workspace ?? "\u0000"));
				if (mine.length) {
					parts.push("hunters:");
					for (const r of mine) {
						const icon = r.state === "stopping" ? "⏹" : r.state === "done" ? "✓" : r.state === "quiet" ? "○" : "●";
						parts.push(`  ${icon} ${r.name} [${r.state}] phase=${r.phase ?? "?"} — ${r.result ? r.result : r.activity ?? ""}`);
					}
				} else {
					parts.push("hunters: (no board rows bound to this workspace)");
				}

				// newest evidence = freshest signs of activity
				try {
					const evd = `${wsPath}/evidence`;
					const stat = async (f: string) => (await fsMod.stat(`${evd}/${f}`)).mtimeMs;
					const evdFiles = (await fsMod.readdir(evd)).filter((f) => !f.startsWith("."));
					const newest = (await Promise.all(evdFiles.map(async (f) => [f, await stat(f)] as const)))
						.sort((a, b) => b[1] - a[1]).slice(0, 5);
					if (newest.length) {
						parts.push("newest evidence:");
						for (const [f, ms] of newest) {
							parts.push(`  ${new Date(ms).toLocaleTimeString()}  ${f}`);
						}
					}
				} catch { /* no evidence dir */ }

				// report tail — the narrative of the stop
				try {
					const repDir = `${wsPath}/reports`;
					const reps = (await fsMod.readdir(repDir)).filter((f) => f.endsWith(".md")).sort();
					if (reps.length) {
						const tail = (await fsMod.readFile(`${repDir}/${reps[reps.length - 1]}`, "utf8")).slice(-800);
						parts.push(`--- report tail (${reps[reps.length - 1]}) ---`, tail);
					} else {
						parts.push("(no report filed yet in this workspace)");
					}
				} catch { /* ignore */ }

				await showReader(ctx, `Workspace ${ws.id}`, parts.join("\n"));
			} catch {
				await ctx.ui.notify(res.stdout + res.stderr, "warning");
			}
		},
	};
	pi.registerCommand("sessions", sepsCmd);
	pi.registerCommand("sepps", { ...sepsCmd, description: `Alias of /sessions` });

	pi.registerCommand("hunt-resume", {
		description: "Resume a hunt: /sepp-resume <session-id> (injects report + session state)",
		handler: async (args: string, ctx: ExtensionContext) => {
			const sid = args.trim().split(/\s+/)[0];
			if (!sid) {
				ctx.ui.notify("Usage: /sepp-resume <session-id>  (see /sepps)", "warning");
				return;
			}
			const p = await run(pi, "python3", [`${SCRIPTS}/workspace.py`, "path", sid]);
			const wsPath = p.stdout.trim();
			if (p.code !== 0 || !wsPath) {
				ctx.ui.notify("No workspace " + sid, "warning");
				return;
			}
			// read session.json + latest report, inject as context
			let sessLine = "";
			let reportTail = "";
			try {
				const fsMod = await import("node:fs/promises");
				const sess = JSON.parse(await fsMod.readFile(`${wsPath}/session.json`, "utf8"));
				sessLine = `Session targets: ${(sess.targets ?? []).map((t: any) => t.url).join(", ")}`;
				currentTarget = sess.targets?.[sess.targets.length - 1]?.url ?? currentTarget;
				setWorkspace(wsPath, ctx);
				const reportsDir = `${wsPath}/reports`;
				const names = (await fsMod.readdir(reportsDir)).filter((f) => f.endsWith(".md")).sort();
				if (names.length > 0) {
					const latest = await fsMod.readFile(`${reportsDir}/${names[names.length - 1]}`, "utf8");
					reportTail = latest.slice(-3500);
				}
			} catch (e) {
				await ctx.ui.notify("resume read problem: " + String(e), "warning");
			}
			const msg = [
				`RESUME HUNT ${sid}`,
				sessLine,
				`Workspace: ${wsPath}`,
				reportTail ? `\n--- latest report (tail) ---\n${reportTail}\n--- end tail ---` : "(no report yet in this workspace)",
				"",
				"Continue the hunt per AGENTS.md: verify the report's open items first, then proceed with the next test class. Same rules as a fresh hunt (read-only probes; approvals for state changes; workspace + findings discipline).",
			].filter(Boolean).join("\n");
			await pi.sendUserMessage(msg);
		},
	});

	pi.registerCommand("prune-hunt-session", {
		description: "Interactive: pick and delete hunt workspaces older than N days (default 30)",
		handler: async (args: string, ctx: ExtensionContext) => {
			const days = parseInt(args.trim(), 10) || 30;
			const res = await run(pi, "python3", [
				`${SCRIPTS}/workspace.py`, "prune", "--days", String(days),
			]);
			try {
				const out = JSON.parse(res.stdout) as { candidates: string[] };
				if (out.candidates.length === 0) {
					await ctx.ui.notify(`Nothing older than ${days} days`, "info");
					return;
				}
				const picked = await ctx.ui.select(
					`Delete workspaces (older than ${days}d)?`,
					out.candidates.concat("CANCEL"),
				);
				if (!picked || picked === "CANCEL") return;
				const del = await run(pi, "python3", [
					`${SCRIPTS}/workspace.py`, "prune", "--days", String(days), "--apply",
				]);
				const out2 = JSON.parse(del.stdout) as { removed: string[] };
				await ctx.ui.notify("Deleted: " + out2.removed.join(", "), "info");
			} catch {
				await ctx.ui.notify(res.stdout + res.stderr, "warning");
			}
		},
	});

	pi.registerCommand("sepp-target-clear", {
		description: "Clear the target panel below the editor",
		handler: async (_args: string, ctx: ExtensionContext) => {
			currentTarget = "";
			lastSeen = "";
			renderWidget(ctx);
			ctx.ui.notify("target panel cleared", "info");
		},
	});

	// ----------------------------------------------------------------- scope
	// /hunt-scope [SITE...] — merge hosts into the dedicated 'sepp' scope via
	// scripts/manage_scope.py. Bare invocation adds the LOCAL TESTBED
	// (127.0.0.1 + localhost) plus the default noise-host denylist; with
	// arguments those sites are merged additionally (glob syntax welcome:
	// *.example.com). Idempotent; never touches operator-made scopes.
	const seppScopeCmd = {
		description: "Scope: /hunt-scope (local testbed) | /hunt-scope <site...> (merge extra hosts)",
		handler: async (args: string, ctx: ExtensionContext) => {
			const sites = (args ?? "").trim().split(/\s+/).filter(Boolean);
			const res = await run(pi, "python3", [`${SCRIPTS}/manage_scope.py`, "ensure", ...sites]);
			const out = (res.stdout || "").trim();
			const err = (res.stderr || "").trim();
			if (res.code !== 0) {
				await ctx.ui.notify(`scope update failed: ${err.slice(0, 200) || `exit ${res.code}`}`, "warning");
				return;
			}
			await showReader(ctx, "SEPP scope", out);
			await ctx.ui.notify("scope ensured (allowlist + denylist merged)", "info");
		},
	};
	pi.registerCommand("hunt-scope", seppScopeCmd);
	pi.registerCommand("sepp-scope", { ...seppScopeCmd, description: `Alias of /hunt-scope` });

	pi.registerCommand("hunt-refresh", {
		description: "Re-harvest freshest target cookie from Caido history",
		handler: async (_args: string, ctx: ExtensionContext) => {
			const hv = await run(pi, "python3", [`${SCRIPTS}/harvest_target.py`]);
			const msg = (hv.stdout + "\n" + hv.stderr).trim();
			await ctx.ui.notify(msg || `harvest exit ${hv.code}`, hv.code === 0 ? "info" : "warning");
		},
	});
}
