/**
 * SEPP top menu (alt+g; SEPP_MENU_KEY override) — the capability hub of the SEPP pi-wrapper.
 *
 * Categories are DATA-DRIVEN so capabilities can be placed without code:
 *   - PROMPTS:     .pi/templates/*.md  (markdown prompt templates; pick one →
 *                  it is pasted into the editor for review/send — nothing fires
 *                  without the operator seeing it)
 *   - HUNT:        the slash commands of the SEPP extensions
 *   - TOOLS:       inspect loaded pi tools + SEPP tooling entry points
 *   - UPDATES:     trigger make setup / pi package refresh (shows output)
 *   - ENGAGEMENT:  brief/status/abort shortcuts (delegate to /sepp-* commands)
 *
 * No globals touched; pure extension. Non-TUI contexts degrade to /menu.
 */
import type { ExtensionAPI, ExtensionContext, Theme } from "@earendil-works/pi-coding-agent";
import * as fs from "node:fs";
import { showReader } from "../lib/reader.ts";
import * as path from "node:path";

// Repo root resolved from THIS file's location (.pi/extensions/x.ts → ../..)
// so the menu works no matter which cwd pi was launched from; process.cwd()
// is only the fallback for loaders without import.meta.url.
function repoRoot(): string {
	try {
		const here = path.dirname(decodeURIComponent(new URL(import.meta.url).pathname));
		const rooted = path.resolve(here, "..", "..");
		return fs.existsSync(path.join(rooted, ".pi")) ? rooted : process.cwd();
	} catch {
		return process.cwd();
	}
}
const REPO = repoRoot();
const TEMPLATES_DIR = path.join(REPO, ".pi", "templates");

interface MenuItem {
	label: string;
	hint?: string;
	action: (ctx: ExtensionContext) => Promise<void>;
}

async function runPi(pi: ExtensionAPI, cmd: string, args: string[], timeout = 120_000) {
	try {
		const res = await pi.exec(cmd, args, { timeout });
		return (res.stdout || "") + (res.stderr ? `\n${res.stderr}` : "");
	} catch (e) {
		return `exec failed: ${String(e)}`;
	}
}

export default function sepMenuExtension(pi: ExtensionAPI) {
	let currentCtx: ExtensionContext | undefined;

	// Shortcut: alt+g by default. ctrl+g is pi's built-in "external editor"
	// (extension shortcuts get skipped on conflict — operator hit this).
	// Override with SEPP_MENU_KEY (validated against pi's KeyId grammar).
	const MENU_KEY = (() => {
		const k = (process.env.SEPP_MENU_KEY ?? "alt+g").trim().toLowerCase();
		return /^[a-z]$/.test(k) ? `alt+${k}` : "alt+g";
	})();

	function templates(): { file: string; body: string }[] {
		try {
			return fs
				.readdirSync(TEMPLATES_DIR)
				.filter((f) => f.endsWith(".md") && !f.startsWith("."))
				.sort()
				.map((f) => ({ file: f, body: fs.readFileSync(path.join(TEMPLATES_DIR, f), "utf8") }));
		} catch {
			return [];
		}
	}

	async function picker(ctx: ExtensionContext, title: string, items: { label: string; hint?: string }[]): Promise<number | undefined> {
		const lines = items.map((i) => (i.hint ? `${i.label}  —  ${i.hint}` : i.label));
		const picked = await ctx.ui.select(`Esc closes · ${title}`, [...lines, "CANCEL"]);
		if (!picked || picked === "CANCEL") return undefined;
		const idx = lines.indexOf(picked);
		return idx >= 0 ? idx : undefined;
	}

	async function menu(ctx: ExtensionContext): Promise<void> {
		const cats: MenuItem[] = [
			{
				label: "Prompt templates",
				hint: ".pi/templates/*.md → into the editor",
				action: async (c) => {
					const tpl = templates();
					if (!tpl.length) {
						await c.ui.notify(`No templates in ${TEMPLATES_DIR} — drop *.md files there`, "warning");
						return;
					}
					const idx = await picker(c, "Prompt template → editor", tpl.map((t) => ({ label: t.file, hint: t.body.split("\n")[0]?.slice(0, 60) })));
					if (idx === undefined) return;
					c.ui.pasteToEditor(tpl[idx].body);
					await c.ui.notify(`"${tpl[idx].file}" pasted into the editor — review and send`, "info");
				},
			},
			{
				label: "Start a hunt session (/hunt-start <url>)",
				hint: "controller → hunter legs",
				action: async (c) => {
					const url = await c.ui.input("Target URL", "https://…");
					if (!url) return;
					c.ui.pasteToEditor(`/hunt-start ${url.trim()} `);
					await c.ui.notify("/sepp prepared in the editor — add context if wanted, then Enter", "info");
				},
			},
			{
				label: "Full red team here (/hunt here <url>)",
				hint: "single-session engagement",
				action: async (c) => {
					const url = await c.ui.input("Target URL", "https://…");
					if (!url) return;
					c.ui.pasteToEditor(`/hunt here ${url.trim()}`);
					await c.ui.notify("/hunt here staged — Enter to launch the engagement", "info");
				},
			},
			{
				label: "Engagement brief (/hunt-brief)",
				hint: "coordinator interprets the hunt",
				action: async (c) => {
					c.ui.pasteToEditor("/sepp-brief");
				},
			},
			{
				label: "Show latest brief",
				hint: "sepp-brief-latest.md in the reader",
				action: async (c) => {
					c.ui.pasteToEditor("/sepp-brief show");
				},
			},
			{
				label: "Live hunter board (/hunters)",
				hint: "who is hunting what, right now",
				action: async (c) => {
					c.ui.pasteToEditor("/hunters");
				},
			},
			{
				label: "Trigger updates",
				hint: "make setup + pi package refresh (shows output)",
				action: async (c) => {
					const confirm = await c.ui.confirm("Updates", "Run 'make setup' now? (installs/verifies tooling — may take a minute)");
					if (!confirm) return;
					await c.ui.notify("make setup running…", "info");
					const out = await runPi(pi, "make", ["setup"], 300_000);
					const tail = out.trim().split("\n").slice(-14).join("\n");
					await showReader(c, "Setup output", tail || "(no output)");
				},
			},
			{
				label: "Tool integration",
				hint: "loaded pi tools + SEPP toolchain paths",
				action: async (c) => {
					const tools = (pi.getAllTools?.() ?? []).map((t: any) => t.name).filter(Boolean);
					const sepp = [
						"caido-cli (proxy traffic) — AGENTS.md cheat-sheet",
						"scripts/hunter_board.py — hunter heartbeat board",
						"scripts/engagement_status.py — deterministic facts",
						"helpers/findings.py — findings ledger (Caido-backed)",
						"drivers/transport.py + adapter contract — garak stack",
					];
					await showReader(c, "Tool integration",
						[`Loaded pi tools (${tools.length}):`, ...(tools.length ? [tools.join(", ")] : ["(none reported)"]), "", "SEPP toolchain:", ...sepp.map((s) => `• ${s}`)].join("\n"),
					);
				},
			},
		];

		for (;;) {
			const idx = await picker(ctx, "SEPP menu — pick a capability", cats.map((m) => ({ label: m.label, hint: m.hint })));
			if (idx === undefined) return;
			await cats[idx].action(ctx);
			// loop back into the menu until CANCEL — acts like a top bar
		}
	}

	pi.registerShortcut(MENU_KEY as any, {
		description: "SEPP menu",
		handler: async (ctx) => {
			currentCtx = ctx;
			await menu(ctx);
		},
	});

	pi.registerCommand("menu", {
		description: `SEPP capability menu (also ${MENU_KEY})`,
		handler: async (_args, ctx) => {
			await menu(ctx);
		},
	});
}
