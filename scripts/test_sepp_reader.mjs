import assert from "node:assert/strict";
import test from "node:test";
import { visibleWidth } from "@earendil-works/pi-tui";
import { showReader } from "../.pi/lib/reader.ts";

test("reader fills the viewport, scrolls on resize and closes by mouse or keyboard", async () => {
  const tui = { terminal: { rows: 16 }, requestRender() {} };
  const theme = { bold: text => text, fg: (_, text) => text };
  let view, closed = 0;
  const ctx = { ui: { custom(factory, options) {
    assert.equal(options.overlay, true);
    view = factory(tui, theme, {}, () => closed++);
  } } };
  await showReader(ctx, "Status board", "界字 🤖\tlong row\n".repeat(40) + "LAST");
  for (const [width, height] of [[80, 24], [20, 8], [9, 4]]) {
    tui.terminal.rows = height;
    const rows = view.render(width);
    assert.equal(rows.length, height);
    assert.ok(rows.every(row => visibleWidth(row) === width));
    assert.ok(rows[0].startsWith("[Close]"));
    view.handleInput("G");
    assert.ok(view.render(width).some(line => line.includes("LAST")));
    view.handleInput("g");
  }
  view.handleMouse({type: "click", button: "left", x: 2, y: 0});
  for (const key of ["\x1b", "q", "\r"]) view.handleInput(key);
  assert.equal(closed, 4);
});
