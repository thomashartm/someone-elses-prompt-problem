import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import register from "./.pi/extensions/local-ollama/index.ts";

test("the active local model appends its own prompt and other providers keep theirs", () => {
  let provider, config, beforeStart;
  register({
    registerProvider(name, options) { provider = name; config = options; },
    on(event, handler) { assert.equal(event, "before_agent_start"); beforeStart = handler; },
  });

  const base = { systemPrompt: "Existing pi instructions" };
  for (const [id, filename] of [
    ["hf.co/mradermacher/DeepHat-V1-7B-GGUF:Q8_0", "deephat.md"],
    ["inkling-2-mini:26b-a5b-nvfp4", "inkling.md"],
  ]) {
    assert.ok(config.models.some(model => model.id === id));
    const expected = readFileSync(new URL("./.pi/extensions/local-ollama/" + filename, import.meta.url), "utf8").trim();
    const result = beforeStart(base, { model: { provider, id } });
    assert.equal(result.systemPrompt, base.systemPrompt + "\n\n" + expected);
    assert.equal(beforeStart(base, { model: { provider: "another-provider", id } }), undefined);
  }
});
