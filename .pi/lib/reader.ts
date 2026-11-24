import type { ExtensionContext } from "@earendil-works/pi-coding-agent";
import { matchesKey, stripTerminalSequences, truncateToWidth, wrapTextWithAnsi } from "@earendil-works/pi-tui";

export async function showReader(ctx: ExtensionContext, title: string, body: string) {
	if (typeof ctx.ui?.custom !== "function") {
		ctx.ui?.notify(body.slice(0, 900), "info");
		return;
	}
	const text = stripTerminalSequences(body).replace(/\t/g, "  ");
	await ctx.ui.custom<void>((tui, theme, _keys, done) => {
		let offset = 0, page = 1, last = 0;
		let wrapWidth = -1;
		let lines: string[] = [];
		const scroll = (value: number) => {
			offset = Math.max(0, Math.min(last, value));
			tui.requestRender();
		};
		return {
			render(width) {
				const height = Math.max(1, tui.terminal.rows);
				const inner = Math.max(1, width - 2);
				if (inner !== wrapWidth) {
					lines = wrapTextWithAnsi(text, inner);
					wrapWidth = inner;
				}
				page = Math.max(0, height - 2);
				last = Math.max(0, lines.length - page);
				offset = Math.min(offset, last);
				const out = [theme.fg("accent", theme.bold("[Close]")) + "  " + theme.bold(title)];
				for (const line of lines.slice(offset, offset + page)) {
					out.push(" " + (line.startsWith("## ") ? theme.fg("accent", theme.bold(line.slice(3))) : line));
				}
				while (out.length < height - 1) out.push("");
				if (height > 1) out.push(theme.fg("muted", " Esc/q close · ↑↓ scroll · " + (offset + 1) + "/" + lines.length));
				return out.map(line => truncateToWidth(line, width, "", true));
			},
			handleInput(key) {
				if (matchesKey(key, "escape") || matchesKey(key, "enter") || key === "q" || key === "\n") done();
				else if (matchesKey(key, "up") || key === "k") scroll(offset - 1);
				else if (matchesKey(key, "down") || key === "j") scroll(offset + 1);
				else if (matchesKey(key, "pageUp")) scroll(offset - page);
				else if (matchesKey(key, "pageDown")) scroll(offset + page);
				else if (key === "g") scroll(0);
				else if (key === "G") scroll(last);
			},
			handleMouse(event) {
				if (event.type === "click" && event.button === "left" && event.y === 0 && event.x >= 0 && event.x < 7) {
					done();
					return { handled: true };
				}
				if (event.type === "wheel") {
					scroll(offset + (event.wheelDelta ?? 0));
					return { handled: true };
				}
			},
			invalidate() { wrapWidth = -1; },
		};
	}, { overlay: true, overlayOptions: { anchor: "center", width: "100%", maxHeight: "100%" } });
}
