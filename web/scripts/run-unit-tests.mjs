import { spawnSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const webRoot = path.resolve(__dirname, "..");
const outDir = path.join(webRoot, ".tmp-unit-tests");
const tscBin = path.join(webRoot, "node_modules", "typescript", "bin", "tsc");
const aliasHook = path.join(webRoot, "tests", "register-alias.cjs");
const compiledTestsDir = path.join(outDir, "tests");

function runOrExit(command, args) {
  const result = spawnSync(command, args, {
    cwd: webRoot,
    stdio: "inherit",
    env: { ...process.env, MANTA_WEB_ROOT: webRoot },
  });
  if (result.error) {
    throw result.error;
  }
  if (result.status !== 0) {
    process.exit(result.status ?? 1);
  }
}

function collectTestFiles(dir) {
  const out = [];
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      out.push(...collectTestFiles(full));
    } else if (entry.name.endsWith(".test.js")) {
      out.push(full);
    }
  }
  return out;
}

try {
  fs.rmSync(outDir, { recursive: true, force: true });
  runOrExit(process.execPath, [tscBin, "-p", "tsconfig.unit.json"]);
  const testFiles = collectTestFiles(compiledTestsDir);
  if (testFiles.length === 0) {
    throw new Error(`No compiled *.test.js files found in ${compiledTestsDir}`);
  }
  runOrExit(process.execPath, ["-r", aliasHook, "--test", ...testFiles]);
} finally {
  fs.rmSync(outDir, { recursive: true, force: true });
}
