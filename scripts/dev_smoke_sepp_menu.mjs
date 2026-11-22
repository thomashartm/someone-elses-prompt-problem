// dev_smoke_sepp_menu.mjs — load-path validation for .pi/extensions/sepp-menu.ts
// Drives the REAL extension code (node --experimental-strip-types) against a
// stubbed ExtensionAPI/ExtensionContext: registration, template paste
// (byte-identical), /sepp + /hunt-here staging, tool inventory, CANCEL loop.
// Picks menu entries by substring from the ACTUAL rendered options.
// Run: NODE_OPTIONS="--experimental-strip-types" node scripts/dev_smoke_sepp_menu.mjs

// Load-path validation harness for .pi/extensions/sepp-menu.ts (v2 — picks by
// substring from the REAL rendered options, like a user does).
import fs from "node:fs";
const reg = { shortcuts: [], commands: [] };
const pasted = [];
const log = [];
let queue = [];   // consume-once: each entry = matcher; when empty → CANCEL
const pick = (t, o) => { const m = queue.length ? queue.shift() : null;
  return m ? (o.find(x => m.test(x)) ?? o[o.length-1]) : o[o.length-1]; };

const piStub = {
	registerShortcut: (shortcut, o) => reg.shortcuts.push({ shortcut, ...o }),
	registerCommand: (name, o) => reg.commands.push({ name, ...o }),
	exec: async (cmd, args) => ({ stdout: `${cmd} ${args.join(" ")}`, stderr: "", code: 0 }),
	getAllTools: () => [{ name: "read" }, { name: "bash" }],
};
const ctxStub = {
	mode: "tui",
	ui: {
		select: async (title, options) => {
			log.push(`SELECT: ${title}`);
			return pick(title, options);
		},
		input: async (title, ph) => "https://t.example",
		confirm: async () => false, // never run make setup inside the test
		notify: (msg, type) => log.push(`NOTIFY(${type}): ${msg.slice(0, 60).replace(/\n/g, " ⏎ ")}`),
		pasteToEditor: (t) => pasted.push(t),
	},
};

const REPO = new URL("..", import.meta.url).pathname.replace(/\/$/, "");
const mod = await import(`${REPO}/.pi/extensions/sepp-menu.ts`);
mod.default(piStub);

const assert = (c, m) => { if (!c) { console.error("FAIL:", m); process.exit(1); } console.log("ok:", m); };

assert(reg.shortcuts.length === 1 && reg.shortcuts[0].shortcut === "alt+g" && typeof reg.shortcuts[0].handler === "function",
	`shortcut alt+g registered w/ handler (${reg.shortcuts.map(s => s.shortcut).join(",")})`);
assert(reg.commands.some(c => c.name === "menu"), "/menu command registered");
const handler = reg.shortcuts[0].handler;

// 1. templates flow (menu → templates → pick file → menu → CANCEL)
queue = [/^Prompt templates/, /tripwire-canary\.md/];
await handler(ctxStub);
const fileBody = fs.readFileSync(`${REPO}/.pi/templates/tripwire-canary.md`, "utf8");
assert(pasted[0] === fileBody, "template pasted BYTE-IDENTICAL into editor");

// 2. hunt staging flow
pasted.length = 0;
queue = [/^Start a hunt/];
await handler(ctxStub);
assert(pasted[0] === "/hunt-start https://t.example ", `"/hunt-start <url>" staged: ${JSON.stringify(pasted[0])}`);

// 3. here-mode staging
pasted.length = 0;
queue = [/^Full red team here/];
await handler(ctxStub);
assert(pasted[0] === "/hunt here https://t.example", `"/hunt here <url>" staged: ${JSON.stringify(pasted[0])}`);

// 4. tool integration (getAllTools + notify path)
queue = [/^Tool integration/];
await handler(ctxStub);
assert(log.some(l => l.includes("Loaded pi tools (2)")), "tool inventory surfaced via notify");

// 5. menu loop terminates on CANCEL without stray actions
log.length = 0; pasted.length = 0; // queue empty → immediate CANCEL
await handler(ctxStub);
assert(log.filter(l => l.startsWith("SELECT")).length === 1 && pasted.length === 0,
	"CANCEL closes the menu cleanly (no residual paste/action)");
console.log("\nALL GREEN — registration, template byte-identical paste, hunt staging, here-mode, tool inventory, cancel loop");
