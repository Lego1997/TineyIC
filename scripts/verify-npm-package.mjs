import { existsSync, readFileSync, statSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = fileURLToPath(new URL("../", import.meta.url));
const metadata = JSON.parse(readFileSync(path.join(root, "package.json"), "utf8"));
const npmLock = JSON.parse(
  readFileSync(path.join(root, "package-lock.json"), "utf8"),
);
const pythonProject = readFileSync(
  path.join(root, "src", "tinyic", "pyproject.toml"),
  "utf8",
);
const pythonVersion = pythonProject.match(/^version\s*=\s*"([^"]+)"/m)?.[1];

function requireCondition(condition, message) {
  if (!condition) {
    throw new Error(message);
  }
}

requireCondition(metadata.name === "tinyic", "npm package name must be tinyic");
requireCondition(
  metadata.version === pythonVersion,
  `npm version ${metadata.version} does not match TinyIC ${pythonVersion}`,
);
requireCondition(
  npmLock.name === metadata.name &&
    npmLock.version === metadata.version &&
    npmLock.packages?.[""]?.version === metadata.version &&
    npmLock.packages?.[""]?.engines?.node === metadata.engines?.node,
  "package-lock.json metadata does not match package.json",
);
requireCondition(
  metadata.bin?.tinyic === "bin/tinyic.mjs",
  "package.json must expose bin/tinyic.mjs as tinyic",
);
for (const field of [
  "dependencies",
  "devDependencies",
  "optionalDependencies",
  "peerDependencies",
  "bundledDependencies",
  "bundleDependencies",
]) {
  requireCondition(!(field in metadata), `npm dependency field ${field} is forbidden`);
}
requireCondition(
  metadata.engines?.node === ">=22.14",
  "npm launcher must support the documented Node.js range",
);
requireCondition(
  metadata.publishConfig?.access === "public" &&
    metadata.publishConfig?.registry === "https://registry.npmjs.org/",
  "npm publication must be public and use the official registry",
);
for (const lifecycle of [
  "preinstall",
  "install",
  "postinstall",
  "prepublish",
  "prepare",
  "preprepare",
  "postprepare",
  "dependencies",
  "predependencies",
  "postdependencies",
]) {
  requireCondition(
    !(lifecycle in (metadata.scripts ?? {})),
    `npm lifecycle hook ${lifecycle} is forbidden`,
  );
}

for (const relative of [
  ".python-version",
  "config.ini",
  "LICENSE",
  "package-lock.json",
  "README.md",
  "pyproject.toml",
  "uv.lock",
  "tinyic.toml",
  "src/tinyic/pyproject.toml",
  "src/tinytroupe/pyproject.toml",
  "src/tinytroupe/LICENSE",
  "src/tinytroupe/FORK.md",
  "docs/assets/tinyic-town-hall.jpg",
  "docs/assets/tinyic-debate-transcript.jpg",
]) {
  requireCondition(existsSync(path.join(root, relative)), `missing ${relative}`);
}

requireCondition(
  readFileSync(path.join(root, ".python-version"), "utf8") === "3.12\n",
  ".python-version must pin the documented Python 3.12 runtime",
);

const launcher = path.join(root, "bin", "tinyic.mjs");
requireCondition((statSync(launcher).mode & 0o111) !== 0, "launcher is not executable");
requireCondition(
  readFileSync(launcher, "utf8").startsWith("#!/usr/bin/env node\n"),
  "launcher is missing its Node shebang",
);
