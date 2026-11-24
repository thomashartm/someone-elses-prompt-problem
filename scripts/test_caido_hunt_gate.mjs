import assert from "node:assert/strict";
import test from "node:test";
import controls from "../.pi/extensions/sepp.ts";
import ledger from "../.pi/extensions/sepp-redteam.ts";

test("unavailable Caido blocks every explicit hunt start before further work", async () => {
  const commands = new Map(), events = new Map(), calls = [], warnings = [];
  const pi = {
    registerCommand: (name, command) => commands.set(name, command),
    registerTool() {},
    on: (name, callback) => events.set(name, callback),
    exec: async (program, args) => {
      calls.push([program, ...args]);
      return {code: 1, stdout: "", stderr: ""};
    },
  };
  const ctx = {ui: {notify: (message) => warnings.push(message)}};
  controls(pi);
  ledger(pi);
  for (const [name, args] of [["sepp", "https://fixture.invalid"], ["hunt-start", "https://fixture.invalid"],
                               ["hunt", "start https://fixture.invalid"], ["hunt", "here https://fixture.invalid"]]) {
    await commands.get(name).handler(args, ctx);
  }
  const input = await events.get("input")({text: "target: https://fixture.invalid"}, ctx);
  assert.equal(input.action, "handled");
  assert.equal(calls.length, 5);
  assert.ok(calls.every(([program, path]) => program === "python3" && path.endsWith("/check_caido.py")));
  assert.equal(warnings.length, 5);
});
