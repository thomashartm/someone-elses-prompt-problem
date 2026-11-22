import { defineConfig } from "vite";

// Frontend bundle: Caido injects the frontend SDK at runtime; nothing else
// to bundle (no UI framework). Mirrors six2dez/dispatch's frontend build.
export default defineConfig({
  build: {
    lib: {
      entry: "src/frontend/index.ts",
      name: "plugin-frontend",
      fileName: () => "script.js",
      formats: ["es"],
    },
    outDir: "dist/frontend",
    minify: false,
    rollupOptions: {
      external: [/^caido:/, "@caido/sdk-frontend"],
    },
  },
});
