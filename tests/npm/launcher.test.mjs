import assert from "node:assert/strict";
import { spawn, spawnSync } from "node:child_process";
import {
  chmod,
  copyFile,
  mkdir,
  mkdtemp,
  readFile,
  readdir,
  realpath,
  rm,
  writeFile,
} from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";

const root = fileURLToPath(new URL("../../", import.meta.url));
const launcher = path.join(root, "bin", "tinyic.mjs");
const fakeUvFixture = path.join(root, "tests", "npm", "fixtures", "fake-uv.mjs");

async function makeTemporaryDirectory(t, label) {
  const directory = await mkdtemp(path.join(tmpdir(), `tinyic-${label}-`));
  t.after(() => rm(directory, { force: true, recursive: true }));
  return directory;
}

async function makeFakeUv(t) {
  const directory = await makeTemporaryDirectory(t, "fake-uv");
  const executable = path.join(directory, "uv");
  await copyFile(fakeUvFixture, executable);
  await chmod(executable, 0o755);
  return directory;
}

function launcherEnvironment(fakeUvDirectory, extra = {}) {
  const environment = { ...process.env };
  delete environment.TINYIC_CONFIG;
  return {
    ...environment,
    TINYIC_NPM_CACHE_DIR: path.join(fakeUvDirectory, "cache"),
    ...extra,
    PATH: `${fakeUvDirectory}${path.delimiter}${process.env.PATH ?? ""}`,
  };
}

function runCaptured(command, arguments_, options) {
  return new Promise((resolve, reject) => {
    const child = spawn(command, arguments_, {
      ...options,
      stdio: ["ignore", "pipe", "pipe"],
    });
    let stdout = "";
    let stderr = "";
    child.stdout.setEncoding("utf8");
    child.stderr.setEncoding("utf8");
    child.stdout.on("data", (chunk) => {
      stdout += chunk;
    });
    child.stderr.on("data", (chunk) => {
      stderr += chunk;
    });
    child.once("error", reject);
    child.once("close", (code, signal) => {
      resolve({ code, signal, stderr, stdout });
    });
  });
}

test("forwards argv without a shell and preserves caller context", async (t) => {
  const fakeUvDirectory = await makeFakeUv(t);
  const caller = await makeTemporaryDirectory(t, "caller space-雪");
  const sentinel = path.join(caller, "must-not-exist");
  const userArguments = [
    "debate",
    "AAPL",
    "--headless",
    "--json",
    "value with spaces",
    "雪",
    "'quoted'",
    `$(touch ${sentinel})`,
  ];
  const result = spawnSync(process.execPath, [launcher, ...userArguments], {
    cwd: caller,
    encoding: "utf8",
    env: launcherEnvironment(fakeUvDirectory),
  });

  assert.equal(result.status, 0, result.stderr);
  const payload = JSON.parse(result.stdout);
  const projectIndex = payload.argv.indexOf("--project") + 1;
  const cachedProject = payload.argv[projectIndex];
  assert.deepEqual(payload.argv, [
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
    ...userArguments,
  ]);
  assert.equal(await realpath(payload.cwd), await realpath(caller));
  assert.ok(path.isAbsolute(cachedProject));
  assert.ok(!cachedProject.startsWith(root));
  assert.equal(payload.tinyicConfig, path.join(cachedProject, "tinyic.toml"));
  assert.equal(
    payload.projectEnvironment,
    path.join(path.dirname(cachedProject), "environment"),
  );
  assert.equal(
    await readFile(path.join(cachedProject, ".python-version"), "utf8"),
    "3.12\n",
  );
  assert.ok(path.isAbsolute(payload.projectEnvironment));
  assert.ok(!payload.projectEnvironment.startsWith(root));
  await assert.rejects(readFile(sentinel));
});

test("honors a caller-local TinyIC configuration", async (t) => {
  const fakeUvDirectory = await makeFakeUv(t);
  const caller = await makeTemporaryDirectory(t, "caller-config");
  await writeFile(path.join(caller, "tinyic.toml"), "default_preset = 'default'\n");

  const result = spawnSync(process.execPath, [launcher, "--version"], {
    cwd: caller,
    encoding: "utf8",
    env: launcherEnvironment(fakeUvDirectory),
  });

  assert.equal(result.status, 0, result.stderr);
  assert.equal(JSON.parse(result.stdout).tinyicConfig, null);
});

test("honors an explicit TinyIC configuration", async (t) => {
  const fakeUvDirectory = await makeFakeUv(t);
  const caller = await makeTemporaryDirectory(t, "explicit-config");
  const config = path.join(caller, "custom.toml");
  await writeFile(config, "default_preset = 'default'\n");

  const result = spawnSync(process.execPath, [launcher, "--version"], {
    cwd: caller,
    encoding: "utf8",
    env: launcherEnvironment(fakeUvDirectory, { TINYIC_CONFIG: config }),
  });

  assert.equal(result.status, 0, result.stderr);
  assert.equal(JSON.parse(result.stdout).tinyicConfig, config);
});

test("serializes one runtime bootstrap for concurrent first launches", async (t) => {
  const fakeUvDirectory = await makeFakeUv(t);
  const syncLog = path.join(fakeUvDirectory, "sync.log");
  const environment = launcherEnvironment(fakeUvDirectory, {
    FAKE_UV_SYNC_DELAY_MS: "300",
    FAKE_UV_SYNC_LOG: syncLog,
  });
  const results = await Promise.all(
    Array.from({ length: 6 }, () =>
      runCaptured(process.execPath, [launcher, "--version"], { env: environment }),
    ),
  );

  const projects = new Set();
  for (const result of results) {
    assert.equal(result.code, 0, result.stderr);
    assert.equal(result.signal, null);
    const payload = JSON.parse(result.stdout);
    projects.add(payload.argv[payload.argv.indexOf("--project") + 1]);
  }
  assert.equal(projects.size, 1);
  const [cachedProject] = projects;
  assert.match(
    await readFile(path.join(cachedProject, ".tinyic-npm-project"), "utf8"),
    /^2\.2\.0-[a-f0-9]{16}-[a-f0-9]{16}\n$/,
  );
  const cacheNamespace = path.dirname(path.dirname(cachedProject));
  assert.deepEqual(
    (await readdir(cacheNamespace)).filter((entry) => entry.startsWith(".")),
    [],
  );
  assert.equal((await readFile(syncLog, "utf8")).trim().split("\n").length, 1);
});

test(
  "fails safely with cache guidance after an unclean bootstrap stop",
  { skip: process.platform === "win32" },
  async (t) => {
    const fakeUvDirectory = await makeFakeUv(t);
    const environment = launcherEnvironment(fakeUvDirectory);
    const first = spawnSync(process.execPath, [launcher, "--version"], {
      encoding: "utf8",
      env: environment,
    });
    assert.equal(first.status, 0, first.stderr);
    const payload = JSON.parse(first.stdout);
    const cachedProject = payload.argv[payload.argv.indexOf("--project") + 1];
    const releaseCache = path.dirname(cachedProject);
    await rm(path.join(releaseCache, "environment"), {
      force: true,
      recursive: true,
    });

    const exited = spawn(process.execPath, ["-e", ""]);
    const deadPid = exited.pid;
    await new Promise((resolve) => exited.once("close", resolve));
    const lock = path.join(releaseCache, "environment.lock");
    await mkdir(lock);
    await writeFile(
      path.join(lock, "owner.json"),
      `${JSON.stringify({ pid: deadPid, token: "stale" })}\n`,
    );

    const result = spawnSync(process.execPath, [launcher, "--version"], {
      encoding: "utf8",
      env: environment,
    });
    assert.equal(result.status, 1);
    assert.equal(result.stdout, "");
    assert.match(
      result.stderr,
      /previous runtime setup stopped unexpectedly;.*remove .* and retry/,
    );
  },
);

test(
  "cleans a partial runtime when bootstrap is interrupted",
  { skip: process.platform === "win32" },
  async (t) => {
    const fakeUvDirectory = await makeFakeUv(t);
    const syncLog = path.join(fakeUvDirectory, "bootstrap-sync.log");
    const environment = launcherEnvironment(fakeUvDirectory, {
      FAKE_UV_SYNC_DELAY_MS: "10000",
      FAKE_UV_SYNC_LOG: syncLog,
    });
    const child = spawn(process.execPath, [launcher, "--version"], {
      env: environment,
      stdio: ["ignore", "pipe", "pipe"],
    });

    let syncStarted = false;
    for (let attempt = 0; attempt < 1_000; attempt += 1) {
      try {
        await readFile(syncLog, "utf8");
        syncStarted = true;
        break;
      } catch {
        await new Promise((resolve) => setTimeout(resolve, 10));
      }
    }
    assert.ok(syncStarted, "fake uv bootstrap did not start");
    child.kill("SIGINT");
    const [code, signal] = await new Promise((resolve) =>
      child.once("close", (...args) => resolve(args)),
    );
    assert.equal(code, 130);
    assert.equal(signal, null);

    const namespace = path.join(
      fakeUvDirectory,
      "cache",
      "tinyic",
      "npm",
    );
    const releases = await readdir(namespace);
    assert.equal(releases.length, 1);
    const release = path.join(namespace, releases[0]);
    await assert.rejects(readFile(path.join(release, "environment.lock", "owner.json")));
    await assert.rejects(readFile(path.join(release, "environment", ".tinyic-npm-runtime")));

    const retry = spawnSync(process.execPath, [launcher, "--version"], {
      encoding: "utf8",
      env: launcherEnvironment(fakeUvDirectory, {
        FAKE_UV_SYNC_DELAY_MS: "0",
      }),
    });
    assert.equal(retry.status, 0, retry.stderr);
  },
);

test("preserves stdin, JSONL stdout, and progress stderr byte for byte", async (t) => {
  const fakeUvDirectory = await makeFakeUv(t);
  const caller = await makeTemporaryDirectory(t, "streams");
  const stdinFile = path.join(caller, "stdin.jsonl");
  const stdin = '{"type":"queue","text":"margin of safety"}\n';
  const stdout = '{"v":1,"seq":1}\n{"v":1,"seq":2}\n';
  const stderr = "preparing locked environment\n";
  const result = spawnSync(process.execPath, [launcher, "debate", "AAPL"], {
    cwd: caller,
    encoding: "utf8",
    env: launcherEnvironment(fakeUvDirectory, {
      FAKE_UV_MODE: "streams",
      FAKE_UV_STDIN_FILE: stdinFile,
      FAKE_UV_STDERR: stderr,
      FAKE_UV_STDOUT: stdout,
    }),
    input: stdin,
  });

  assert.equal(result.status, 0);
  assert.equal(result.stdout, stdout);
  assert.equal(result.stderr, stderr);
  assert.equal(await readFile(stdinFile, "utf8"), stdin);
  for (const line of result.stdout.trim().split("\n")) {
    assert.doesNotThrow(() => JSON.parse(line));
  }
});

for (const exitCode of [0, 2, 3, 42]) {
  test(`propagates exit status ${exitCode}`, async (t) => {
    const fakeUvDirectory = await makeFakeUv(t);
    const result = spawnSync(process.execPath, [launcher, "--version"], {
      encoding: "utf8",
      env: launcherEnvironment(fakeUvDirectory, {
        FAKE_UV_EXIT: String(exitCode),
      }),
    });
    assert.equal(result.status, exitCode, result.stderr);
  });
}

test("reports a missing uv executable on stderr only", async (t) => {
  const emptyPath = await makeTemporaryDirectory(t, "empty-path");
  const environment = {
    ...process.env,
    PATH: emptyPath,
    TINYIC_NPM_CACHE_DIR: path.join(emptyPath, "cache"),
  };
  delete environment.TINYIC_CONFIG;
  const result = spawnSync(process.execPath, [launcher, "--version"], {
    encoding: "utf8",
    env: environment,
  });

  assert.equal(result.status, 127);
  assert.equal(result.stdout, "");
  assert.equal(
    result.stderr,
    "tinyic: uv is required; install it from https://docs.astral.sh/uv/getting-started/installation/\n",
  );
});

const signals = ["SIGINT", "SIGTERM"];
if (process.platform !== "win32") {
  signals.push("SIGHUP");
}
for (const signal of signals) {
  test(`forwards ${signal} to uv without orphaning it`, async (t) => {
    const fakeUvDirectory = await makeFakeUv(t);
    const directory = await makeTemporaryDirectory(t, `signal-${signal}`);
    const readyFile = path.join(directory, "ready");
    const signalFile = path.join(directory, "signal");
    const child = spawn(process.execPath, [launcher, "debate", "AAPL"], {
      env: launcherEnvironment(fakeUvDirectory, {
        FAKE_UV_MODE: "signal",
        FAKE_UV_READY_FILE: readyFile,
        FAKE_UV_SIGNAL_FILE: signalFile,
      }),
      stdio: ["ignore", "pipe", "pipe"],
    });

    let childPid;
    for (let attempt = 0; attempt < 1_000; attempt += 1) {
      try {
        childPid = Number(await readFile(readyFile, "utf8"));
        break;
      } catch {
        await new Promise((resolve) => setTimeout(resolve, 10));
      }
    }
    assert.ok(Number.isInteger(childPid) && childPid > 0, "fake uv did not start");
    child.kill(signal);
    const [code] = await new Promise((resolve) => child.once("close", (...args) => resolve(args)));
    assert.equal(code, { SIGINT: 72, SIGTERM: 73, SIGHUP: 74 }[signal]);
    assert.equal((await readFile(signalFile, "utf8")).trim(), signal);
    await new Promise((resolve) => setTimeout(resolve, 25));
    assert.throws(() => process.kill(childPid, 0), { code: "ESRCH" });
  });
}
