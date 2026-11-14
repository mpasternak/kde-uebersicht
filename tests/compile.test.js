// Compiles widgets with the exact esbuild flags the daemon uses and evaluates
// the result the way the runtime does.
import { test } from "node:test";
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { readFileSync, readdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { evaluateWidget } from "../runtime/core.js";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const FLAGS = JSON.parse(readFileSync(join(ROOT, "app/esbuild-flags.json"), "utf8"));
const ESBUILD = join(ROOT, "node_modules/.bin/esbuild");

function compile(file) {
  return execFileSync(ESBUILD, [file, ...FLAGS], { cwd: dirname(file), encoding: "utf8" });
}

// Minimal stand-ins for the runtime's globals.
const fakeReact = { createElement: (type, props, ...children) => ({ type, props, children }), Fragment: "F" };
globalThis.__ubsReact = fakeReact;
const fakeUebersicht = { run: () => Promise.resolve(""), css: (s) => `css(${String(s).trim()})`, React: fakeReact };
const requireFake = (name) => {
  if (name === "uebersicht") return fakeUebersicht;
  throw new Error(`unexpected require(${name})`);
};

test("Übersicht-style widget with relative import compiles and evaluates", () => {
  const mod = evaluateWidget(compile(join(ROOT, "tests/fixtures/widgets/stateful.jsx")), "stateful", requireFake);
  assert.equal(mod.command, "echo hi");
  assert.equal(mod.refreshFrequency, false);
  assert.equal(mod.ran, fakeUebersicht.run, "imports resolve to the runtime's uebersicht module");
  assert.equal(mod.updateState({ type: "INC" }, mod.initialState).n, 1);
  const tree = mod.render({ output: "hi", n: 2 }, () => {});
  assert.equal(tree.type, "F");
  assert.deepEqual(tree.children[0].children, ["clicks: 2"]);
});

test("every example widget compiles", () => {
  const dir = join(ROOT, "examples");
  for (const f of readdirSync(dir).filter((f) => f.endsWith(".jsx"))) {
    const mod = evaluateWidget(compile(join(dir, f)), f, requireFake);
    assert.equal(typeof mod.render, "function", `${f} exports render`);
  }
});

test("syntax errors surface as esbuild failures", () => {
  assert.throws(() =>
    execFileSync(ESBUILD, ["--loader=jsx", ...FLAGS.filter((f) => f !== "--bundle")], {
      input: "export const x = <div>;",
      encoding: "utf8",
      stdio: "pipe",
    })
  );
});
