/**
 * /findings [args...] — run the findings ledger (helpers/findings.py).
 *
 * Read-only subcommands (list, show, introspect) run immediately:
 *   /findings                 — list (default)
 *   /findings list --host X   — filtered list
 *   /findings show <id>       — one finding
 *   /findings introspect      — dump what Caido actually accepts
 * `create` writes to the ledger / Caido, so it asks for confirmation first.
 *
 * Output renders in a scrollable reader dialog (notify() is too dim for
 * walls of ledger text).
 */
import type { ExtensionAPI, ExtensionContext } from "@earendil-works/pi-coding-agent";
import * as fs from "node:fs";
import { showReader } from "../lib/reader.ts";
import * as path from "node:path";

function repoRoot(): string {
	try {
		const here = path.dirname(decodeURIComponent(new URL(import.meta.url).pathname));
		const rooted = path.resolve(here, "..", "..");
		return fs.existsSync(path.join(rooted, ".pi")) ? rooted : process.cwd();
	} catch {
		return process.cwd();
	}
}

/** Shell-like tokenizer: double/single quotes, no escapes needed inside them. */
function tokenize(text: string): string[] {
	const out: string[] = [];
	const re = /"([^"]*)"|'([^']*)'|(\S+)/g;
	let m: RegExpExecArray | null;
	while ((m = re.exec(text)) !== null) {
		if (m[1] !== undefined) out.push(m[1]);
		else if (m[2] !== undefined) out.push(m[2]);
		else if (m[3] !== undefined) out.push(m[3]);
	}
	return out;
}

export default function findingsExtension(pi: ExtensionAPI) {
	const REPO = repoRoot();
	const SCRIPT = path.join(REPO, "helpers", "findings.py");


	pi.registerCommand("findings", {
		description: "Findings ledger: /findings [list] [--host H] · /findings show <id> · /findings introspect · /findings create ... (asks)",
		handler: async (args: string, ctx: ExtensionContext) => {
			const argv = tokenize(args ?? "");
			if (!argv[0] || argv[0].startsWith("-")) argv.unshift("list");

			const sub = argv[0];
			if (sub === "create") {
				const ok = await ctx.ui.confirm(
					"Findings ledger",
					"Run findings.py create — this writes to the ledger / Caido. Proceed?",
				);
				if (!ok) {
					await ctx.ui.notify("findings: create cancelled", "info");
					return;
				}
			}

			let stdout = "";
			let stderr = "";
			let code = 0;
			try {
				const res = await pi.exec("python3", [SCRIPT, ...argv], { timeout: 120_000 });
				stdout = res.stdout ?? "";
				stderr = res.stderr ?? "";
				code = res.code ?? 0;
			} catch (e) {
				stderr = String(e);
				code = 127;
			}

			const body = [
				stdout.trim(),
				stderr.trim() ? `--- stderr ---\n${stderr.trim()}` : "",
				code !== 0 ? `(exit ${code})` : "",
			].filter(Boolean).join("\n") || "(no output)";

			await showReader(ctx, `findings ${argv.join(" ")}`, body);
		},
	});
}
