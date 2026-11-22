import { defineConfig } from "vite";

// Backend bundle: ESM, native node builtins stay external (Caido's backend
// runtime provides them). Mirrors six2dez/dispatch's backend build.
export default defineConfig({
  build: {
    lib: {
      entry: "src/backend/index.ts",
      name: "plugin-backend",
      fileName: () => "script.js",
      formats: ["es"],
    },
    outDir: "dist/backend",
    minify: false,
    rollupOptions: {
      external: [/^caido:/, "child_process", "fs", "os", "path"],
    },
  },
});
