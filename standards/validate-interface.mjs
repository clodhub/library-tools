#!/usr/bin/env node
// Validates interface documents with each standard's own tooling.
//   OpenRPC 1.3   (key "openrpc")   @open-rpc/schema-utils-js validateOpenRPCDocument
//   OpenAPI 3.1   (key "openapi")   @apidevtools/swagger-parser validate
//   AsyncAPI 3.1  (key "asyncapi")  @asyncapi/parser (errors only; warnings printed)
//   JSON Schema 2020-12 (key "$schema" https://json-schema.org/draft/2020-12/schema)  ajv 2020 strict compile
// Every document also names its interface: OpenRPC/OpenAPI/AsyncAPI info.title "ae/<name>" and info.version;
// JSON Schema "title" "ae/<name>" and "x-version". Prints one JSON line per file; exit 1 when any file fails.
// Usage: node validate-interface.mjs FILE...
import { readFileSync } from "node:fs";
import { validateOpenRPCDocument } from "@open-rpc/schema-utils-js";
import SwaggerParser from "@apidevtools/swagger-parser";
import { Parser } from "@asyncapi/parser";
import Ajv2020 from "ajv/dist/2020.js";
import addFormats from "ajv-formats";

const TYPE = /^ae\/[a-z][a-z0-9-]*$/;
const VERSION = /^\d+\.\d+\.\d+$/;

function identity(doc, kind) {
  const title = kind === "jsonschema" ? doc.title : doc.info?.title;
  const version = kind === "jsonschema" ? doc["x-version"] : doc.info?.version;
  const errors = [];
  if (!TYPE.test(String(title ?? ""))) errors.push(`title must be the interface type ae/<name>, got ${JSON.stringify(title)}`);
  if (!VERSION.test(String(version ?? ""))) errors.push(`version must be three integers, got ${JSON.stringify(version)}`);
  return { type: title, version, errors };
}

export function kindOf(doc) {
  if (typeof doc.openrpc === "string") return "openrpc";
  if (typeof doc.openapi === "string") return "openapi";
  if (typeof doc.asyncapi === "string") return "asyncapi";
  if (doc.$schema === "https://json-schema.org/draft/2020-12/schema") return "jsonschema";
  return null;
}

export async function validate(doc) {
  const kind = kindOf(doc);
  if (!kind) return { kind: null, errors: ["not a recognised standard document (openrpc, openapi, asyncapi, or JSON Schema 2020-12)"] };
  const id = identity(doc, kind);
  const errors = [...id.errors];
  const warnings = [];
  try {
    if (kind === "openrpc") {
      if (!/^1\.3\.\d+$/.test(doc.openrpc)) errors.push(`openrpc must be 1.3.x, got ${doc.openrpc}`);
      const result = validateOpenRPCDocument(structuredClone(doc));
      if (result !== true) errors.push(String(result?.message ?? result));
    } else if (kind === "openapi") {
      if (!/^3\.1\.\d+$/.test(doc.openapi)) errors.push(`openapi must be 3.1.x, got ${doc.openapi}`);
      await SwaggerParser.validate(structuredClone(doc));
    } else if (kind === "asyncapi") {
      if (!/^3\.1\.\d+$/.test(doc.asyncapi)) errors.push(`asyncapi must be 3.1.x, got ${doc.asyncapi}`);
      const { diagnostics } = await new Parser().parse(JSON.stringify(doc));
      for (const d of diagnostics) (d.severity === 0 ? errors : warnings).push(`${d.code}: ${d.message} at ${d.path.join(".")}`);
    } else {
      const ajv = new Ajv2020({ strict: true, allErrors: true, keywords: ["x-version", "x-ourapps", "x-ourapps-modules"] });
      addFormats(ajv);
      ajv.compile(doc);
    }
  } catch (error) {
    errors.push(String(error?.message ?? error));
  }
  return { kind, type: id.type, version: id.version, errors, warnings };
}

if (import.meta.url === `file://${process.argv[1]}`) {
  let failed = 0;
  for (const file of process.argv.slice(2)) {
    let result;
    try {
      result = await validate(JSON.parse(readFileSync(file, "utf8")));
    } catch (error) {
      result = { kind: null, errors: [`unreadable: ${error.message}`] };
    }
    if (result.errors.length) failed++;
    console.log(JSON.stringify({ file, ok: result.errors.length === 0, ...result }));
  }
  process.exit(failed ? 1 : 0);
}
