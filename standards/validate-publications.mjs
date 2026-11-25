#!/usr/bin/env node
// Validate archive-derived publication bodies with the catalogue's shared rules, without network or database writes.
// Usage: node tools/standards/validate-publications.mjs CATALOGUE_CHECKOUT PUBLICATIONS.json
import { readFileSync } from "node:fs";
import { resolve, join } from "node:path";
import { pathToFileURL } from "node:url";

const [checkout, input] = process.argv.slice(2);
if (!checkout || !input) throw new Error("provide the catalogue checkout and publication file");
const root = resolve(checkout);
const { build } = await import(pathToFileURL(join(root, "node_modules/esbuild/lib/main.js")).href);
const output = await build({ entryPoints: [join(root, "shared/src/index.ts")], bundle: true, format: "esm", platform: "node", write: false, logLevel: "silent" });
const catalogue = await import(`data:text/javascript;base64,${Buffer.from(output.outputFiles[0].text).toString("base64")}`);
const publications = JSON.parse(readFileSync(input === "-" ? 0 : input, "utf8"));
const components = new Map();
let failures = 0;
for (const body of publications.components) {
  const result = catalogue.validateComponentEntry(body);
  console.log(JSON.stringify({ name: body.name, ok: result.ok, errors: result.ok ? [] : result.errors }));
  if (!result.ok) failures++;
  else components.set(`${body.name} ${body.version}`, {
    ...result.entry,
    profiles: result.entry.suitability.profiles.map(p => ({ id: p.id, ...(p.settings ? { settings: p.settings } : {}) })),
  });
}
for (const body of publications.recipes) {
  const result = catalogue.validateRecipeEntry(body, {
    components: { source: "validated archive publications", find: (name, version) => components.get(`${name} ${version}`) ?? null },
  });
  console.log(JSON.stringify({ name: body.recipe.name, ok: result.ok, errors: result.ok ? [] : result.errors }));
  if (!result.ok) failures++;
}
process.exitCode = failures ? 1 : 0;
