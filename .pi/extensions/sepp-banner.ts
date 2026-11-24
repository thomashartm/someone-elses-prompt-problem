/**
 * SEPP branded header for pi (replaces the stock logo/hint banner IN THE TUI).
 *
 * Branding contract (operator): this is a SEPP-branded tool that EMBEDS pi —
 * the header must say so, show the hunting capabilities, and credit pi.
 * Uses pi's supported setHeader() hook; no global files, no theme changes.
 * Non-TUI modes (print/json/rpc) are untouched.
 */
import type { ExtensionAPI, Theme } from "@earendil-works/pi-coding-agent";
import { truncateToWidth } from "@earendil-works/pi-tui";
import { VERSION } from "@earendil-works/pi-coding-agent";

// hotkey suffix for the hint line — mirrors sepp-menu.ts env contract
// (alt+g default; SEPP_MENU_KEY=<letter> overrides)
const MENU_KEY_SUFFIX =
	process.env.SEPP_MENU_KEY && /^[a-z]$/i.test(process.env.SEPP_MENU_KEY)
		? " / alt+" + process.env.SEPP_MENU_KEY.toLowerCase()
		: "";

// figlet "ANSI Shadow"-style SEPP wordmark (5 rows, 8-col glyphs, 2-col gaps).
// The P has a real bowl (right-side stem joining top bar to mid bar) — the
// earlier draft lacked it and read "SEF" (operator report).
const SEP_MARK = [
	[" ██████ ", "███████ ", "███████ "],
	["██      ", "██      ", "██    ██"],
	["███████ ", "███████ ", "███████ "],
	["      ██", "██      ", "██      "],
	["███████ ", "███████ ", "██      "],
];

function sepWordmark(theme: Theme): string[] {
	return SEP_MARK.map((row) =>
		row
			.map((cell) => theme.fg("accent", cell))
			.join("  "),
	);
}

export default function sepBannerExtension(pi: ExtensionAPI) {
	pi.on("session_start", async (_event, ctx) => {
		if (ctx.mode !== "tui" || typeof ctx.ui?.setHeader !== "function") return;
		ctx.ui.setHeader((_tui: unknown, theme: Theme) => ({
			render(width: number): string[] {
				const mark = sepWordmark(theme);
				// right column, aligned to the 5 wordmark rows
				const side: string[][] = [
					[theme.bold("Somebody Else's Problem"), theme.fg("muted", "  …or so")],
					[theme.fg("muted", "LLM security red-team station — embeds "), `pi v${VERSION}`],
					[],
					[theme.fg("muted", "hunting station")],
					[theme.fg("muted", "controller ↔ hunters via pi-subagents")],
				];
				// one command per line — combined rows were hard to distinguish (operator)
				const caps: [string, string][] = [
					["/menu", "capability hub — every command below, guided"],
					["/hunt-start <url>", "new hunt session: pi delegates legs to background hunters"],
					["/hunt here <url>", "new hunt session executed inside THIS chat (no subagents)"],
					["/hunters", "live board: who is hunting what right now"],
					["/hunt-status", "deterministic facts: ledger-anchored attack surface"],
					["/hunt-brief", "coordinator-interpreted engagement brief"],
					["/sessions", "past & current hunt sessions: what was done, where stopped"],
					["/hunt-abort", "stop all live hunters immediately"],
				];
				const lines: string[] = [""];
				for (let i = 0; i < mark.length; i++) {
					lines.push(`  ${mark[i]}   ${(side[i] ?? []).join("")}`);
				}
				const cmdWidth = Math.max(...caps.map(([c]) => c.length)) + 2;
				for (const [cmd, desc] of caps) {
					lines.push(`  ${theme.fg("accent", cmd.padEnd(cmdWidth))}${theme.fg("muted", "· " + desc)}`);
				}
				lines.push("");
				lines.push(
					"  " +
					theme.fg("accent", theme.bold("/menu")) +
					theme.fg("muted", "  \u00b7  start here \u2014 capability menu  \u00b7  ctrl+o full help") +
					theme.fg("dim", `  \u00b7 SEPP embeds pi (hotkey alt+g${MENU_KEY_SUFFIX})`),
				);
				return lines.map(line => truncateToWidth(line, width));
			},
			invalidate() {},
		}));
	});
}
