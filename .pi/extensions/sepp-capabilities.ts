/**
 * SEPP capabilities sheet (/capabilities, alias /caps) — renders the
 * machine-derived capability sheet from scripts/capabilities.py:
 *
 *   - potency ladder with the battery profiles each level may spend
 *   - garak battery profiles (predicted/ceiling/probe lists)
 *   - every registered agent-shell slash command with its description
 *   - make targets + workspace lifecycle one-liners
 *
 * Everything shown is PARSED from the repo by capabilities.py (AGENTS.md
 * ladder table, configs/garak_profiles.json, registerCommand sites,
 * Makefile) — the sheet cannot promise something the repo does not have.
 * Falls back to printing the sheet when the reader pane is unavailable.
 */
import type { ExtensionAPI, ExtensionContext } from "@earendil-works/pi-coding-agent";
import { showReader } from "../lib/reader.ts";

export default function sepCapabilitiesExtension(pi: ExtensionAPI) {
	async function render(ctx: ExtensionContext): Promise<string> {
		try {
			const res = await pi.exec("python3", ["scripts/capabilities.py"], { timeout: 60_000 });
			const out = ((res.stdout || "") + (res.stderr || "")).trim();
			return out || "capabilities.py produced no output";
		} catch (e) {
			return `capabilities sheet unavailable: ${String(e)}\n\n(manual fallback: python3 scripts/capabilities.py)`;
		}
	}

	const capsCmd = {
		description: "Capability sheet: potency ladder + battery binding, garak profiles, all slash commands, make targets",
		handler: async (_args: string, ctx: ExtensionContext) => {
			const body = await render(ctx);
			try {
				await showReader(ctx, "capabilities", body);
			} catch {
				console.log(body); // non-TUI fallback
			}
		},
	};

	pi.registerCommand("capabilities", capsCmd);
	pi.registerCommand("caps", { ...capsCmd, description: "Alias of /capabilities" });
}
