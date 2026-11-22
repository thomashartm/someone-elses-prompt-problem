// Packs dist/ + manifest.json + icon.svg into plugin_package.zip.
// Same archive layout as six2dez/dispatch's pack step (manifest at zip root,
// compiled entrypoints under frontend/ and backend/).
import JSZip from "jszip";
import { existsSync, readFileSync, readdirSync, statSync, writeFileSync } from "fs";
import { join } from "path";

function addDir(zip, dirPath, zipPath) {
  for (const entry of readdirSync(dirPath)) {
    if (entry.endsWith(".zip") || entry.endsWith(".sig")) continue;
    const full = join(dirPath, entry);
    const zipped = zipPath ? `${zipPath}/${entry}` : entry;
    if (statSync(full).isDirectory()) addDir(zip, full, zipped);
    else zip.file(zipped, readFileSync(full));
  }
}

const zip = new JSZip();
const manifestRaw = readFileSync("manifest.json", "utf8");
const manifest = JSON.parse(manifestRaw);

// Validate BEFORE packing: Caido rejects packages with a vague "manifest was
// invalid" toast, so catch structural breakage here with precise messages.
const errs = [];
for (const key of ["id", "name", "version", "plugins"]) {
  if (manifest[key] === undefined) errs.push(`missing required key: ${key}`);
}
if (Array.isArray(manifest.plugins) && manifest.plugins.length > 0) {
  const ids = new Set(manifest.plugins.map((p) => p.id).filter(Boolean));
  for (const p of manifest.plugins) {
    for (const key of ["kind", "id", "name", "entrypoint"]) {
      if (p[key] === undefined) errs.push(`plugin '${p.id ?? "? "}': missing key ${key}`);
    }
    if (p.entrypoint && !existsSync(join("dist", p.entrypoint))) {
      errs.push(`plugin '${p.id}': entrypoint dist/${p.entrypoint} not built`);
    }
    // Cross-reference integrity (the class of bug behind the invalid manifest):
    // a frontend's backend ref must name an existing sibling plugin id.
    if (p.backend?.id !== undefined && !ids.has(p.backend.id)) {
      errs.push(`plugin '${p.id}': backend.id '${p.backend.id}' references no sibling plugin (have: ${[...ids].join(", ")})`);
    }
  }
}
if (errs.length) {
  console.error("manifest.json is INVALID — refusing to pack:");
  for (const e of errs) console.error("  - " + e);
  process.exit(1);
}

zip.file("manifest.json", manifestRaw);
if (manifest.icon && existsSync(manifest.icon)) {
  zip.file(manifest.icon, readFileSync(manifest.icon));
}
if (existsSync("dist")) addDir(zip, "dist", "");

const buf = await zip.generateAsync({ type: "nodebuffer" });
writeFileSync("dist/plugin_package.zip", buf);
console.log("Created dist/plugin_package.zip");
