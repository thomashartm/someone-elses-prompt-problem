import type { ExtensionAPI, ExtensionContext } from "@earendil-works/pi-coding-agent";
import { fileURLToPath } from "node:url";

const check = fileURLToPath(new URL("../../scripts/check_caido.py", import.meta.url));

export async function requireCaido(pi: ExtensionAPI, ctx: ExtensionContext): Promise<boolean> {
	try {
		const result = await pi.exec("python3", [check], { timeout: 6500 });
		if (result.code === 0 && !result.killed) return true;
	} catch { /* A missing or timed-out preflight also blocks dispatch. */ }
	ctx.ui.notify("Hunt blocked. Check the Caido instance and authentication with caido-cli status.", "error");
	return false;
}
