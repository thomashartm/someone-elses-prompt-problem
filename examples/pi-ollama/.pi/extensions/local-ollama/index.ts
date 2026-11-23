import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { readFileSync } from "node:fs";

const provider = "local-ollama";
const deepHat = "hf.co/mradermacher/DeepHat-V1-7B-GGUF:Q8_0";
const inkling = "inkling-2-mini:26b-a5b-nvfp4";

export default function (pi: ExtensionAPI) {
  // Read both before registration so a broken profile is visible at startup.
  const prompts = new Map([
    [deepHat, readFileSync(new URL("./deephat.md", import.meta.url), "utf8").trim()],
    [inkling, readFileSync(new URL("./inkling.md", import.meta.url), "utf8").trim()],
  ]);
  for (const [model, prompt] of prompts) {
    if (!prompt) throw new Error(`Empty Ollama system prompt for ${model}`);
  }

  const limits = { contextWindow: 32768, maxTokens: 4096 };
  const cost = { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 };
  pi.registerProvider(provider, {
    name: "Local Ollama",
    baseUrl: "http://127.0.0.1:11434/v1",
    api: "openai-completions",
    apiKey: "ollama",
    models: [
      { id: deepHat, name: "DeepHat V1 7B Q8_0", reasoning: false, input: ["text"], cost, ...limits },
      { id: inkling, name: "Inkling 2 Mini NVFP4", reasoning: true, input: ["text", "image"], cost, ...limits },
    ],
  });

  pi.on("before_agent_start", (event, ctx) => {
    if (ctx.model?.provider !== provider) return;
    const prompt = prompts.get(ctx.model.id);
    if (prompt) return { systemPrompt: event.systemPrompt + "\n\n" + prompt };
  });
}
