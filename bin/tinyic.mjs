#!/usr/bin/env node

import { spawn } from "node:child_process";
import { createHash, randomUUID } from "node:crypto";
import {
  cpSync,
  copyFileSync,
  existsSync,
  lstatSync,
  mkdirSync,
  mkdtempSync,
  readFileSync,
  readdirSync,
  renameSync,
  rmSync,
  writeFileSync,
} from "node:fs";
import { constants as osConstants, homedir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const packageRoot = fileURLToPath(new URL("../", import.meta.url));
const packageMetadata = JSON.parse(
  readFileSync(path.join(packageRoot, "package.json"), "utf8"),
);

function sourceFilter(source) {
  const basename = path.basename(source);
  return !(
    basename === "build" ||
    basename === "__pycache__" ||
    basename.endsWith(".egg-info") ||
    basename.endsWith(".pyc")
  );
}

function parseIni(text, label) {
  const sections = new Map();
  let current = null;
  for (const [index, line] of text.split(/\r?\n/).entries()) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith("#") || trimmed.startsWith(";")) {
      continue;
    }
    const sectionMatch = trimmed.match(/^\[([^\]]+)]$/);
    if (sectionMatch) {
      const name = sectionMatch[1].trim();
      const normalized = name.toLowerCase();
      if (sections.has(normalized)) {
        throw new Error(`${label}:${index + 1}: duplicate INI section ${name}`);
      }
      current = { name, options: new Map() };
      sections.set(normalized, current);
      continue;
    }
    const separator = line.indexOf("=");
    if (!current || separator < 1) {
      throw new Error(`${label}:${index + 1}: unsupported INI line`);
    }
    const name = line.slice(0, separator).trim();
    const normalized = name.toLowerCase();
    if (current.options.has(normalized)) {
      throw new Error(`${label}:${index + 1}: duplicate INI option ${name}`);
    }
    current.options.set(normalized, {
      name,
      value: line.slice(separator + 1).trim(),
    });
  }
  return sections;
}

function mergedTinyTroupeConfig(baseText, overlayText) {
  const base = parseIni(baseText, "src/tinytroupe/config.ini");
  const overlay = parseIni(overlayText, "config.ini");
  for (const [sectionName, overlaySection] of overlay) {
    const baseSection = base.get(sectionName);
    if (!baseSection) {
      throw new Error(`config.ini contains unknown section ${overlaySection.name}`);
    }
    for (const [optionName, option] of overlaySection.options) {
      const baseOption = baseSection.options.get(optionName);
      if (!baseOption) {
        throw new Error(
          `config.ini contains unknown option ${overlaySection.name}.${option.name}`,
        );
      }
      baseOption.value = option.value;
    }
  }
  const lines = [];
  for (const section of base.values()) {
    lines.push(`[${section.name}]`);
    for (const option of section.options.values()) {
      lines.push(`${option.name}=${option.value}`);
    }
    lines.push("");
  }
  return `${lines.join("\n")}\n`;
}

function addPathToHash(hash, absolute, relative) {
  if (!sourceFilter(absolute)) {
    return;
  }
  const stat = lstatSync(absolute);
  if (stat.isSymbolicLink()) {
    throw new Error(`runtime source cannot be a symbolic link: ${relative}`);
  }
  if (stat.isDirectory()) {
    hash.update(`directory\0${relative}\0`);
    for (const entry of readdirSync(absolute).sort()) {
      addPathToHash(hash, path.join(absolute, entry), path.posix.join(relative, entry));
    }
    return;
  }
  if (!stat.isFile()) {
    throw new Error(`unsupported runtime source entry: ${relative}`);
  }
  hash.update(`file\0${relative}\0`);
  hash.update(readFileSync(absolute));
  hash.update("\0");
}

function packagedSourceHash() {
  const hash = createHash("sha256");
  for (const relative of [
    ".python-version",
    "bin/tinyic.mjs",
    "config.ini",
    "package.json",
    "pyproject.toml",
    "src/tinyic",
    "src/tinytroupe",
    "tinyic.toml",
  ]) {
    addPathToHash(hash, path.join(packageRoot, relative), relative);
  }
  return hash.digest("hex").slice(0, 16);
}

const lockHash = createHash("sha256")
  .update(readFileSync(path.join(packageRoot, "uv.lock")))
  .digest("hex")
  .slice(0, 16);
const sourceHash = packagedSourceHash();

function cacheRoot(environment) {
  if (environment.TINYIC_NPM_CACHE_DIR) {
    return path.resolve(environment.TINYIC_NPM_CACHE_DIR);
  }
  if (process.platform === "win32") {
    const localAppData = environment.LOCALAPPDATA;
    return localAppData && path.isAbsolute(localAppData)
      ? localAppData
      : path.join(homedir(), "AppData", "Local");
  }
  if (process.platform === "darwin") {
    return path.join(homedir(), "Library", "Caches");
  }
  const xdgCache = environment.XDG_CACHE_HOME;
  return xdgCache && path.isAbsolute(xdgCache)
    ? xdgCache
    : path.join(homedir(), ".cache");
}

const childEnvironment = { ...process.env };
const environmentKey = `${packageMetadata.version}-${lockHash}-${sourceHash}`;
const cacheNamespace = path.join(
  cacheRoot(childEnvironment),
  "tinyic",
  "npm",
);
const releaseCache = path.join(cacheNamespace, environmentKey);
const cachedProject = path.join(releaseCache, "project");
const runtimeEnvironment = path.join(releaseCache, "environment");
const bootstrapLock = path.join(releaseCache, "environment.lock");
const bootstrapOwner = path.join(bootstrapLock, "owner.json");
const projectMarker = ".tinyic-npm-project";
const runtimeMarker = ".tinyic-npm-runtime";
const expectedMarker = `${environmentKey}\n`;

function markerMatches(root, marker) {
  try {
    return readFileSync(path.join(root, marker), "utf8") === expectedMarker;
  } catch {
    return false;
  }
}

function stageProject(staging) {
  for (const filename of [
    ".python-version",
    "config.ini",
    "pyproject.toml",
    "tinyic.toml",
    "uv.lock",
  ]) {
    copyFileSync(path.join(packageRoot, filename), path.join(staging, filename));
  }
  mkdirSync(path.join(staging, "src"));
  for (const packageName of ["tinyic", "tinytroupe"]) {
    cpSync(
      path.join(packageRoot, "src", packageName),
      path.join(staging, "src", packageName),
      {
        errorOnExist: true,
        filter: sourceFilter,
        force: false,
        preserveTimestamps: true,
        recursive: true,
      },
    );
  }
  const vendoredConfig = path.join(staging, "src", "tinytroupe", "config.ini");
  writeFileSync(
    vendoredConfig,
    mergedTinyTroupeConfig(
      readFileSync(vendoredConfig, "utf8"),
      readFileSync(path.join(staging, "config.ini"), "utf8"),
    ),
    "utf8",
  );
  writeFileSync(path.join(staging, projectMarker), expectedMarker, {
    encoding: "utf8",
    flag: "wx",
  });
}

function prepareCachedProject() {
  if (markerMatches(cachedProject, projectMarker)) {
    return;
  }

  mkdirSync(releaseCache, { recursive: true });
  const staging = mkdtempSync(path.join(cacheNamespace, `.${environmentKey}-`));
  try {
    stageProject(staging);
    try {
      renameSync(staging, cachedProject);
    } catch (error) {
      // Another first launch may have won the atomic rename race.
      if (!markerMatches(cachedProject, projectMarker)) {
        throw error;
      }
    }
  } finally {
    rmSync(staging, { force: true, recursive: true });
  }
}

const forwardedSignals =
  process.platform === "win32"
    ? ["SIGINT", "SIGTERM"]
    : ["SIGINT", "SIGTERM", "SIGHUP"];
const signalHandlers = new Map();
let activeChild = null;
let requestedSignal = null;

function signalExitCode(signal) {
  const signalNumber = signal ? osConstants.signals[signal] : undefined;
  return typeof signalNumber === "number" ? 128 + signalNumber : 1;
}

for (const signal of forwardedSignals) {
  const handler = () => {
    requestedSignal ??= signal;
    if (activeChild?.exitCode === null && activeChild.signalCode === null) {
      try {
        activeChild.kill(signal);
      } catch {
        // The child may already have received the terminal signal directly.
      }
    }
  };
  signalHandlers.set(signal, handler);
  process.on(signal, handler);
}

function removeSignalHandlers() {
  for (const [signal, handler] of signalHandlers) {
    process.removeListener(signal, handler);
  }
}

function throwIfInterrupted() {
  if (requestedSignal) {
    const error = new Error(`interrupted by ${requestedSignal}`);
    error.signal = requestedSignal;
    throw error;
  }
}

function delay(milliseconds) {
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
}

function runUv(arguments_, options) {
  return new Promise((resolve, reject) => {
    const child = spawn("uv", arguments_, {
      cwd: options.cwd,
      env: options.env,
      shell: false,
      stdio: options.stdio,
      windowsHide: false,
    });
    activeChild = child;
    if (requestedSignal) {
      try {
        child.kill(requestedSignal);
      } catch {
        // The child may fail to spawn or terminate before the signal arrives.
      }
    }
    child.once("error", (error) => {
      if (activeChild === child) {
        activeChild = null;
      }
      reject(error);
    });
    child.once("exit", (code, signal) => {
      if (activeChild === child) {
        activeChild = null;
      }
      resolve({ code, signal });
    });
  });
}

function tryAcquireBootstrapLock() {
  const token = randomUUID();
  try {
    mkdirSync(bootstrapLock);
  } catch (error) {
    if (error?.code === "EEXIST") {
      return null;
    }
    throw error;
  }
  try {
    writeFileSync(
      bootstrapOwner,
      `${JSON.stringify({ pid: process.pid, started_at: Date.now(), token })}\n`,
      { encoding: "utf8", flag: "wx" },
    );
  } catch (error) {
    rmSync(bootstrapLock, { force: true, recursive: true });
    throw error;
  }
  return token;
}

function releaseBootstrapLock(token) {
  try {
    const owner = JSON.parse(readFileSync(bootstrapOwner, "utf8"));
    if (owner.token === token) {
      rmSync(bootstrapLock, { force: true, recursive: true });
    }
  } catch {
    // A ready canonical environment is authoritative even if lock cleanup fails.
  }
}

function readBootstrapOwner() {
  try {
    return JSON.parse(readFileSync(bootstrapOwner, "utf8"));
  } catch {
    return null;
  }
}

function processIsAlive(pid) {
  if (!Number.isInteger(pid) || pid <= 0) {
    return false;
  }
  try {
    process.kill(pid, 0);
    return true;
  } catch (error) {
    return error?.code !== "ESRCH";
  }
}

async function buildRuntime() {
  if (markerMatches(runtimeEnvironment, runtimeMarker)) {
    return;
  }

  const stagingRoot = mkdtempSync(path.join(releaseCache, ".runtime-"));
  const stagingProject = path.join(stagingRoot, "project");
  let runtimeReady = false;
  try {
    cpSync(cachedProject, stagingProject, {
      errorOnExist: true,
      filter: sourceFilter,
      force: false,
      preserveTimestamps: true,
      recursive: true,
    });
    rmSync(runtimeEnvironment, { force: true, recursive: true });
    const bootstrapEnvironment = {
      ...childEnvironment,
      UV_PROJECT_ENVIRONMENT: runtimeEnvironment,
    };
    const result = await runUv(
      [
        "sync",
        "--project",
        stagingProject,
        "--frozen",
        "--no-dev",
        "--no-editable",
        "--package",
        "tinyic",
      ],
      {
        cwd: process.cwd(),
        env: bootstrapEnvironment,
        stdio: ["ignore", "ignore", "inherit"],
      },
    );
    throwIfInterrupted();
    if (result.code !== 0) {
      const suffix = result.signal
        ? `signal ${result.signal}`
        : `exit status ${result.code}`;
      throw new Error(`uv could not create the locked runtime (${suffix})`);
    }
    writeFileSync(path.join(runtimeEnvironment, runtimeMarker), expectedMarker, {
      encoding: "utf8",
      flag: "wx",
    });
    runtimeReady = true;
  } finally {
    rmSync(stagingRoot, { force: true, recursive: true });
    if (!runtimeReady) {
      rmSync(runtimeEnvironment, { force: true, recursive: true });
    }
  }

  if (!markerMatches(runtimeEnvironment, runtimeMarker)) {
    throw new Error("the locked runtime was not promoted into the cache");
  }
}

async function ensureRuntime() {
  if (markerMatches(runtimeEnvironment, runtimeMarker)) {
    return;
  }

  const lockToken = tryAcquireBootstrapLock();
  if (lockToken) {
    try {
      await buildRuntime();
    } finally {
      releaseBootstrapLock(lockToken);
    }
    return;
  }

  const waitStarted = Date.now();
  let missingOwnerSince = null;
  while (!markerMatches(runtimeEnvironment, runtimeMarker)) {
    throwIfInterrupted();
    if (!existsSync(bootstrapLock)) {
      return ensureRuntime();
    }
    const owner = readBootstrapOwner();
    if (owner) {
      missingOwnerSince = null;
      if (!processIsAlive(owner.pid)) {
        throw new Error(
          `a previous runtime setup stopped unexpectedly; stop any remaining uv process, remove ${releaseCache}, and retry`,
        );
      }
    } else {
      missingOwnerSince ??= Date.now();
      if (Date.now() - missingOwnerSince > 2_000) {
        throw new Error(
          `the runtime setup lock is incomplete; remove ${releaseCache} and retry`,
        );
      }
    }
    if (Date.now() - waitStarted > 30 * 60_000) {
      throw new Error(
        `runtime setup is still locked after 30 minutes; inspect process ${owner?.pid ?? "unknown"}, remove ${releaseCache} when safe, and retry`,
      );
    }
    await delay(100);
  }
}

async function main() {
  try {
    prepareCachedProject();
    await ensureRuntime();
    throwIfInterrupted();
  } catch (error) {
    if (error?.signal) {
      process.exitCode = signalExitCode(error.signal);
      return;
    }
    if (error?.code === "ENOENT" && error?.path === "uv") {
      process.stderr.write(
        "tinyic: uv is required; install it from https://docs.astral.sh/uv/getting-started/installation/\n",
      );
      process.exitCode = 127;
      return;
    }
    process.stderr.write(
      `tinyic: failed to prepare its local runtime cache: ${error.message}\n`,
    );
    process.exitCode = 1;
    return;
  }

  childEnvironment.UV_PROJECT_ENVIRONMENT = runtimeEnvironment;
  const callerConfig = path.join(process.cwd(), "tinyic.toml");
  if (!childEnvironment.TINYIC_CONFIG && !existsSync(callerConfig)) {
    childEnvironment.TINYIC_CONFIG = path.join(cachedProject, "tinyic.toml");
  }

  const uvArguments = [
    "run",
    "--project",
    cachedProject,
    "--frozen",
    "--no-dev",
    "--no-editable",
    "--no-sync",
    "--package",
    "tinyic",
    "--",
    "tinyic",
    ...process.argv.slice(2),
  ];

  try {
    const result = await runUv(uvArguments, {
      cwd: process.cwd(),
      env: childEnvironment,
      stdio: "inherit",
    });
    process.exitCode =
      typeof result.code === "number"
        ? result.code
        : signalExitCode(result.signal);
  } catch (error) {
    if (error?.code === "ENOENT") {
      process.stderr.write(
        "tinyic: uv is required; install it from https://docs.astral.sh/uv/getting-started/installation/\n",
      );
      process.exitCode = 127;
      return;
    }
    process.stderr.write(`tinyic: failed to start uv: ${error.message}\n`);
    process.exitCode = 1;
  }
}

try {
  await main();
} finally {
  removeSignalHandlers();
}
