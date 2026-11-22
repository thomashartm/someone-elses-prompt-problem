/**
 * Runtime smoke for .pi/extensions/sepp-redteam.ts — stubbed ExtensionAPI.
 * Run: node scripts/dev_smoke_sepp_redteam.mjs happy | restore | corrupt | pin
 *
 * NOTE: the extension snapshots $SEPP_REDTEAM_LEDGER_DIR at import time, so the
 * mode is resolved and env set BEFORE the module is imported.
 */
const log = (...a) => console.log("[smoke]", ...a);
const tools = {}, commands = {}, handlers = {};

const mode = process.argv[2] ?? "happy";
if (mode === "pin") process.env.SEPP_REDTEAM_LEDGER_DIR = "/tmp/sepp-smoke-pin-fallback";
const { default: ext } = await import("../.pi/extensions/sepp-redteam.ts");

const pi = {
	registerTool: (d) => (tools[d.name] = d),
	registerCommand: (n, d) => (commands[n] = d),
	on: (ev, h) => (handlers[ev] ??= []).push(h),
	appendEntry: (t, d) => log("appendEntry", t, JSON.stringify(d).slice(0, 80)),
	sendUserMessage: async (m) => log("sendUserMessage ▶", m.split("\n")[0]),
	events: { emit() {}, on() {} },
	setActiveTools() {}, getAllTools: () => [], getActiveTools: () => [],
	exec: async () => ({ stdout: "", stderr: "", code: 0 }),
};

	ext(pi);

	const WS = "/tmp/sepp-smoke-ws";
	{ // scenario independence: stale artifacts from a previous run must not
	  // leak into leak-assertions (root cause of the false "pin did not hold" FAIL)
		const fs0 = await import("node:fs");
		fs0.rmSync(WS, { recursive: true, force: true });
		fs0.mkdirSync(WS, { recursive: true });
	}
	const ui = { notify: async (m, t) => log(`notify(${t ?? "info"})`, m), select: async () => {}, setWidget: () => {}, custom: async () => {} };
	const branch = [];
	const ctx = { ui, cwd: process.cwd(), sessionManager: { getBranch: () => branch } };
	if (mode === "happy" || mode === "restore") {
		branch.push({ type: "custom", customType: "sepp-target-state", data: { currentWorkspace: WS } });
	}

	const fireEvent = async (ev) => { for (const h of handlers[ev] ?? []) await h({}, ctx); };

	await fireEvent("session_start");

if (mode === "happy") {
	await commands.hunt.handler("start https://shop.local/api", ctx);
	await tools.sepp_record_finding.execute("tc1", {
		type: "bola", severity: "high", status: "proven",
		endpoint: "https://shop.local/api/orders/42", claim: "orders readable cross-tenant",
		evidence: "GET /orders/42 as tenant A returned tenant B order (req 1041 vs 1042)",
		requestId: "1042",
	}, {}, {}, ctx);
	await tools.sepp_coordinate.execute("tc2", { action: "set_phase", phase: "prove" }, {}, {}, ctx);
	await tools.sepp_coordinate.execute("tc3", { action: "coverage", addTested: ["auth"], addPending: ["idor-sweep"] }, {}, {}, ctx);
	await commands.hunt.handler("status", ctx);
	await commands.hunt.handler("", ctx); // bare — must NOT be a no-op
	await commands.hunt.handler("report", ctx);

	const fs = await import("node:fs");
	const phase = JSON.parse(fs.readFileSync(`${WS}/phase.json`, "utf8"));
	const ledger = JSON.parse(fs.readFileSync(`${WS}/findings.json`, "utf8"));
	log("DISK phase.json:", phase.phase, phase.target);
	log("DISK findings.json:", ledger.findings.length, "finding(s); coverage:", JSON.stringify(ledger.coverage));
	if (phase.phase !== "prove" || ledger.findings.length !== 1) { log("FAIL"); process.exit(1); }
	const reports = fs.readdirSync("data/reports").filter((f) => f.startsWith("sepp-redteam-"));
	log("REPORT:", reports.at(-1));
	log("HAPPY-PATH OK");
} else if (mode === "restore") {
	// restore mode: workspace holds a VALID ledger; empty session state → hydrate
	const fs = await import("node:fs");
	fs.mkdirSync(WS, { recursive: true });
	fs.writeFileSync(`${WS}/findings.json`, JSON.stringify({
		target: "https://shop.local/api",
		coverage: { tested: ["auth"], pending: [], blocked: [] },
		findings: [{
			id: "SR-prev-1", type: "bola", severity: "high", status: "proven",
			endpoint: "https://shop.local/api/orders/42", claim: "prior session finding",
			evidence: "req pair 1041/1042", requestId: "1042", recordedBy: "hunter:t1",
			timestamp: Date.now(),
		}],
	}, null, 2));
	fs.writeFileSync(`${WS}/phase.json`, JSON.stringify({ phase: "record", target: "https://shop.local/api" }));
	await fireEvent("session_start"); // must announce restoration
	await commands.hunt.handler("status", ctx);
	log("RESTORE-PATH OK if status above shows 1 finding + record phase");
} else if (mode === "pin") {
	// pin mode: arm with NO workspace → flush goes to fallback dir and PINNS there;
	// a workspace appearing mid-session must NOT split the writes.
	const fs = await import("node:fs");
	const FB = "/tmp/sepp-smoke-pin-fallback";
	fs.rmSync(FB, { recursive: true, force: true }); // stale FB would fake a "restored" ledger
	process.env.SEPP_REDTEAM_LEDGER_DIR = FB; // env override so the operator's live ledger is safe
	await commands.hunt.handler("start https://pin.test/api", ctx); // arm: no WS entry yet, env beats all
	if (!fs.existsSync(`${FB}/phase.json`)) { log("FAIL: initial flush not in env dir"); process.exit(1); }
	// now a workspace "appears" (as /sepp would do mid-session)
	branch.push({ type: "custom", customType: "sepp-target-state", data: { currentWorkspace: WS } });
	await tools.sepp_coordinate.execute("tc-p", { action: "coverage", addTested: ["late-note"] }, {}, {}, ctx);
	const fb = JSON.parse(fs.readFileSync(`${FB}/findings.json`, "utf8"));
	if (!fb.coverage.tested.includes("late-note")) { log("FAIL: pin did not hold"); process.exit(1); }
	if (fs.existsSync(`${WS}/phase.json`)) { log("FAIL: writes leaked into the newer workspace"); process.exit(1); }
	log("PIN OK — mid-session workspace appearance did not split the ledger");
	fs.rmSync(FB, { recursive: true, force: true });
} else {
	// corrupt mode: workspace holds a broken findings.json; empty session state
	const fs = await import("node:fs");
	fs.mkdirSync(WS, { recursive: true });
	fs.writeFileSync(`${WS}/findings.json`, "{ oops");
	await fireEvent("session_start"); // must warn loudly, not crash, not reset silently
	const data = fs.readFileSync(`${WS}/findings.json`, "utf8");
	if (data !== "{ oops") { log("FAIL: corrupt file was overwritten"); process.exit(1); }
	log("CORRUPT-PATH OK (loud warn, file untouched)");
}
process.exit(0);
