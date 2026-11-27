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
 *
 * Non-TUI modes untouched. Hide/show anytime with /navbar.
 */
import type { ExtensionAPI, ExtensionContext, Theme } from "@earendil-works/pi-coding-agent";
import { truncateToWidth } from "@earendil-works/pi-tui";

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

function navbarLines(theme: Theme, width: number): string[] {
	const seg = (key: string, name: string) => {
		const letter = key.replace("alt+", "");
		return theme.fg("accent", `⌥${letter} `) + theme.bold(name);
	};
	const segments = [
		theme.fg("accent", theme.bold("SEPP")),
		...CATEGORY_KEY.map((c) => seg(c.key, c.name)),
		seg("alt+a", "All"),
	];
	const bar = segments.join(theme.fg("muted", "  ▸  "));
	const hint = theme.fg("dim", "/navbar hide · alt+g menu · ctrl+o help");
	return [` ${bar}`, ` ${hint}`];
}

function navbarComponent(theme: Theme) {
	return {
		render(width: number): string[] {
			return navbarLines(theme, width).map((l) => truncateToWidth(l, width));
		},
	};
}

// ---------------------------------------------------------------- extension

export default function sepNavbarExtension(pi: ExtensionAPI) {

	pi.on("session_start", async (_event, ctx) => {
		if (ctx.mode !== "tui" || !navbarVisible) return;
		ctx.ui.setWidget(WIDGET_KEY, (tui, theme) => navbarComponent(theme), {
			placement: "aboveEditor",
		});
	});

	async function openCategory(cat: { name: string; items: NavItem[] }, ctx: ExtensionContext) {
		const item = await picker(ctx, `SEPP · ${cat.name}`, cat.items);
		if (!item) return;
		await dispatch(item, ctx, pi);
	}

	pi.registerShortcut("alt+h" as any, {
		description: "SEPP nav: Hunt",
		handler: async (ctx) => openCategory(CATEGORY_KEY[0], ctx),
	});
	pi.registerShortcut("alt+b" as any, {
		description: "SEPP nav: Board",
		handler: async (ctx) => openCategory(CATEGORY_KEY[1], ctx),
	});
	pi.registerShortcut("alt+m" as any, {
		description: "SEPP nav: Maintain",
		handler: async (ctx) => openCategory(CATEGORY_KEY[2], ctx),
	});
	pi.registerShortcut("alt+a" as any, {
		description: "SEPP nav: all commands",
		handler: async (ctx) => openCategory({ name: "All commands", items: ALL_ITEMS }, ctx),
	});

	pi.registerCommand("navbar", {
		description: "Toggle the SEPP navigation bar above the editor",
		handler: async (_args: string, ctx: ExtensionContext) => {
			navbarVisible = !navbarVisible;
			ctx.ui.setWidget(
				WIDGET_KEY,
				navbarVisible ? (_tui, theme) => navbarComponent(theme) : undefined,
				{ placement: "aboveEditor" },
			);
			await ctx.ui.notify(navbarVisible ? "SEPP navbar shown" : "SEPP navbar hidden", "info");
		},
	});
}
