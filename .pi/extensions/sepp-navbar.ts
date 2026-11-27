/**
 * SEPP navigation bar — persistent branded command bar ABOVE the editor.
 *
 * Operator contract: "hunt commands always ready". Categories with hotkeys;
 * picking a command either stages it in the editor (when it needs input) or
 * executes it immediately (when it doesn't — /hunt-abort included, operator
 * decision: no extra confirm; the command's own picker governs targeting).
 *
 * Mechanics (verified against pi 1.6.0 sources, not docs):
 *  - Bar: ctx.ui.setWidget(key, factory, { placement: "aboveEditor" })
 *  - Hotkeys: pi.registerShortcut("alt+x") — chords can't collide with typing
 *  - Immediate exec: pi.sendUserMessage(cmd, { expandPromptTemplates: true })
 *    → agent-session.prompt() dispatches the extension command and RETURNS
 *    BEFORE starting an agent run (no wasted LLM turn)
 *  - Stage: ctx.ui.pasteToEditor(text) — same review-then-Enter flow as /menu
 *  - Mouse: TuiAltScreen (fullscreen, pi's default) enables SGR mouse
 *    reporting; components receive clicks with local coords — same mechanism
 *    as pi's clickable built-in-header logo. Regular mode runs without mouse
 *    reporting (terminal-owned), so hotkeys remain the fallback there.
 *
 * Non-TUI modes untouched. Hide/show anytime with /navbar.
 */
import type { ExtensionAPI, ExtensionContext, Theme } from "@earendil-works/pi-coding-agent";
import { truncateToWidth, visibleWidth } from "@earendil-works/pi-tui";

type ActionKind =
	| { kind: "direct"; cmd: string }
	| { kind: "stage"; build: (c: ExtensionContext) => Promise<string | undefined> };

interface NavItem {
	label: string;
	desc: string;
	action: ActionKind;
}

const WIDGET_KEY = "sepp-navbar";
let navbarVisible = true;

// ------------------------------------------------------------------ items
// Classification per command source (sepp.ts / sepp-findings.ts /
// sepp-redteam.ts): zero-arg commands execute; commands that consume
// operator input are staged in the editor for review-then-Enter.

const HUNT_ITEMS: NavItem[] = [
	{
		label: "Start hunt session",
		desc: "/hunt-start <url> → controller + background hunters",
		action: {
			kind: "stage",
			build: async (c) => {
				const url = await c.ui.input("Target URL", "https://…");
				if (!url?.trim()) return undefined;
				return `/hunt-start ${url.trim()} `;
			},
		},
	},
	{
		label: "Red team here",
		desc: "/hunt here <url> → single-session engagement",
		action: {
			kind: "stage",
			build: async (c) => {
				const url = await c.ui.input("Target URL", "https://…");
				if (!url?.trim()) return undefined;
				return `/hunt here ${url.trim()}`;
			},
		},
	},
	{
		label: "Resume hunt",
		desc: "/hunt-resume <session-id> → report + state reinjected",
		action: {
			kind: "stage",
			build: async (c) => {
				const sid = await c.ui.input("Session id (see Sessions)", "<session-id>");
				if (!sid?.trim()) return undefined;
				return `/hunt-resume ${sid.trim()}`;
			},
		},
	},
	{
		label: "Abort hunters",
		desc: "/hunt-abort → picker (cooperative stop / kill sweep)",
		action: { kind: "direct", cmd: "/hunt-abort" },
	},
	{
		label: "Refresh session cookie",
		desc: "/hunt-refresh → re-harvest freshest cookie from Caido",
		action: { kind: "direct", cmd: "/hunt-refresh" },
	},
	{
		label: "Ensure scope (local testbed)",
		desc: "/hunt-scope → 127.0.0.1/localhost + denylist merged",
		action: { kind: "direct", cmd: "/hunt-scope" },
	},
	{
		label: "Red-team ledger status",
		desc: "/hunt → phases, findings count, ledger path",
		action: { kind: "direct", cmd: "/hunt" },
	},
];

const BOARD_ITEMS: NavItem[] = [
	{ label: "Hunter board", desc: "/hunters → who is hunting what, drill in", action: { kind: "direct", cmd: "/hunters" } },
	{ label: "Engagement status", desc: "/hunt-status → deterministic ledger facts", action: { kind: "direct", cmd: "/hunt-status" } },
	{ label: "Coordinator brief", desc: "/hunt-brief → interpreted engagement brief", action: { kind: "direct", cmd: "/hunt-brief" } },
	{ label: "Latest brief", desc: "/sepp-brief show → sepp-brief-latest.md", action: { kind: "direct", cmd: "/sepp-brief show" } },
	{ label: "Sessions / workspaces", desc: "/sessions → past & current hunts, drill in", action: { kind: "direct", cmd: "/sessions" } },
	{ label: "Findings ledger", desc: "/findings → list recorded findings", action: { kind: "direct", cmd: "/findings" } },
	{ label: "Capabilities", desc: "/capabilities → what this station can do", action: { kind: "direct", cmd: "/capabilities" } },
];

const MAINTAIN_ITEMS: NavItem[] = [
	{
		label: "Prune old workspaces",
		desc: "/prune-hunt-session → pick & delete >30d (self-confirming)",
		action: { kind: "direct", cmd: "/prune-hunt-session" },
	},
	{ label: "Clear target panel", desc: "/sepp-target-clear → reset panel below editor", action: { kind: "direct", cmd: "/sepp-target-clear" } },
];

const CATEGORY_KEY: { key: string; name: string; items: NavItem[] }[] = [
	{ key: "alt+h", name: "Hunt", items: HUNT_ITEMS },
	{ key: "alt+b", name: "Board", items: BOARD_ITEMS },
	{ key: "alt+m", name: "Maintain", items: MAINTAIN_ITEMS },
];

const ALL_ITEMS: NavItem[] = [
	...CATEGORY_KEY.flatMap((c) => c.items.map((i) => ({ ...i, desc: `[${c.name}] ${i.desc}` }))),
];

// ----------------------------------------------------------------- helpers

async function picker(c: ExtensionContext, title: string, items: NavItem[]): Promise<NavItem | undefined> {
	const lines = items.map((i) => `${i.label}  —  ${i.desc}`);
	const picked = await c.ui.select(`Esc closes · ${title}`, [...lines, "CANCEL"]);
	if (!picked || picked === "CANCEL") return undefined;
	const idx = lines.indexOf(picked);
	return idx >= 0 ? items[idx] : undefined;
}

async function dispatch(item: NavItem, c: ExtensionContext, pi: ExtensionAPI): Promise<void> {
	if (item.action.kind === "direct") {
		// Extension command → dispatched, no agent turn (verified in
		// agent-session.js: _tryExecuteExtensionCommand → early return).
		pi.sendUserMessage(item.action.cmd, { expandPromptTemplates: true });
		await c.ui.notify(`${item.action.cmd} dispatched`, "info");
		return;
	}
	const text = await item.action.build(c);
	if (!text) return;
	c.ui.pasteToEditor(text);
	await c.ui.notify(`${text.trim()} staged in the editor — Enter to launch`, "info");
}

// ------------------------------------------------------------------ the bar

function categoryOpener(items: NavItem[], name: string) {
	return async (ctx: ExtensionContext) => {
		const item = await picker(ctx, `SEPP · ${name}`, items);
		if (!item) return;
		await dispatch(item, ctx, currentApi());
	};
}

/** Module-level API handle: set at extension load, before any render can click. */
let apiRef: ExtensionAPI | undefined;
function currentApi(): ExtensionAPI {
	if (!apiRef) throw new Error("sepp-navbar: no extension api yet");
	return apiRef;
}

/**
 * Column hit-map for one render: styled segment widths measured with
 * visibleWidth (ANSI-safe), leading pad included, separators excluded.
 */
function buildBar(
	theme: Theme,
): { line: string; targets: { x0: number; x1: number; name: string }[] } {
	const seg = (key: string, name: string) => {
		const letter = key.replace("alt+", "");
		return theme.fg("accent", `⌥${letter} `) + theme.bold(name);
	};
	const defs: { name: string; styled: string }[] = [
		{ name: "SEPP", styled: theme.fg("accent", theme.bold("SEPP")) },
		...CATEGORY_KEY.map((c) => ({ name: c.name, styled: seg(c.key, c.name) })),
		{ name: "All", styled: seg("alt+a", "All") },
	];
	const separator = theme.fg("muted", "  ▸  ");
	const sepW = visibleWidth(separator);

	const targets: { x0: number; x1: number; name: string }[] = [];
	let x = 1; // leading pad " "
	const parts: string[] = [" "];
	for (const d of defs) {
		const w = visibleWidth(d.styled);
		targets.push({ x0: x, x1: x + w, name: d.name });
		parts.push(d.styled, separator);
		x += w + sepW;
	}
	return { line: parts.join(""), targets };
}

function navbarComponent(theme: Theme, ctx: ExtensionContext) {
	let hits: { x0: number; x1: number; open: (c: ExtensionContext) => Promise<void> }[] = [];

	const openers: Record<string, (c: ExtensionContext) => Promise<void>> = {
		SEPP: categoryOpener(ALL_ITEMS, "All commands"),
		Hunt: categoryOpener(HUNT_ITEMS, "Hunt"),
		Board: categoryOpener(BOARD_ITEMS, "Board"),
		Maintain: categoryOpener(MAINTAIN_ITEMS, "Maintain"),
		All: categoryOpener(ALL_ITEMS, "All commands"),
	};

	return {
		render(width: number): string[] {
			const bar = buildBar(theme);
			hits = bar.targets.map((t) => ({ x0: t.x0, x1: t.x1, open: openers[t.name] })).filter((t) => t.open);
			const hint = theme.fg("dim", "click · /navbar hide · alt+g menu · ctrl+o help");
			return [bar.line, ` ${hint}`].map((l) => truncateToWidth(l, width));
		},
		handleMouse(event: { type: string; x: number; y: number }): { handled: boolean } | undefined {
			if (event.type !== "click" || event.y !== 0) return undefined;
			const hit = hits.find((h) => event.x >= h.x0 && event.x < h.x1);
			if (!hit) return undefined;
			void hit.open(ctx);
			return { handled: true };
		},
	};
}

// ---------------------------------------------------------------- extension

export default function sepNavbarExtension(pi: ExtensionAPI) {
	apiRef = pi;

	pi.on("session_start", async (_event, ctx) => {
		if (ctx.mode !== "tui" || !navbarVisible) return;
		ctx.ui.setWidget(WIDGET_KEY, (_tui, theme) => navbarComponent(theme, ctx), {
			placement: "aboveEditor",
		});
	});

	// Click targets and keyboard shortcuts share one opener factory.
	// categoryOpener resolves the extension API lazily via currentApi().

	pi.registerShortcut("alt+h" as any, {
		description: "SEPP nav: Hunt",
		handler: async (ctx) => categoryOpener(HUNT_ITEMS, "Hunt")(ctx),
	});
	pi.registerShortcut("alt+b" as any, {
		description: "SEPP nav: Board",
		handler: async (ctx) => categoryOpener(BOARD_ITEMS, "Board")(ctx),
	});
	pi.registerShortcut("alt+m" as any, {
		description: "SEPP nav: Maintain",
		handler: async (ctx) => categoryOpener(MAINTAIN_ITEMS, "Maintain")(ctx),
	});
	pi.registerShortcut("alt+a" as any, {
		description: "SEPP nav: all commands",
		handler: async (ctx) => categoryOpener(ALL_ITEMS, "All commands")(ctx),
	});

	pi.registerCommand("navbar", {
		description: "Toggle the SEPP navigation bar above the editor",
		handler: async (_args: string, ctx: ExtensionContext) => {
			navbarVisible = !navbarVisible;
			ctx.ui.setWidget(
				WIDGET_KEY,
				navbarVisible ? (_tui, theme) => navbarComponent(theme, ctx) : undefined,
				{ placement: "aboveEditor" },
			);
			await ctx.ui.notify(navbarVisible ? "SEPP navbar shown" : "SEPP navbar hidden", "info");
		},
	});
}
