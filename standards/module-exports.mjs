#!/usr/bin/env node
// Lists the names each TypeScript entry module exports, read with the TypeScript compiler API (nothing is imported or run).
// Re-exports (`export * from`, `export { a } from`) are followed. Prints one JSON line per file: {file, ok, exports, errors}.
// Usage: node module-exports.mjs FILE...
import ts from "typescript";
import { existsSync } from "node:fs";
import { resolve } from "node:path";

const files = process.argv.slice(2).map((file) => resolve(file));
const program = ts.createProgram(files.filter((file) => existsSync(file)), {
  allowJs: true, noEmit: true, skipLibCheck: true, noResolve: false, allowImportingTsExtensions: true,
  module: ts.ModuleKind.NodeNext, moduleResolution: ts.ModuleResolutionKind.NodeNext, target: ts.ScriptTarget.ESNext,
  types: [],
});
const checker = program.getTypeChecker();
let failed = 0;
for (const file of files) {
  const result = { file, ok: false, exports: [], errors: [] };
  const source = program.getSourceFile(file);
  if (!source) {
    result.errors.push("entry module missing");
  } else {
    const symbol = checker.getSymbolAtLocation(source);
    if (symbol) result.exports = checker.getExportsOfModule(symbol).map((item) => item.getName()).sort();
    result.ok = true;
  }
  if (!result.ok) failed++;
  console.log(JSON.stringify(result));
}
process.exit(failed ? 1 : 0);
