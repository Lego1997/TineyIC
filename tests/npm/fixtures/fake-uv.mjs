#!/usr/bin/env node

import {
  appendFileSync,
  mkdirSync,
  readFileSync,
  writeFileSync,
} from "node:fs";
import path from "node:path";

const mode = process.env.FAKE_UV_MODE ?? "inspect";

if (process.argv[2] === "sync") {
  const environment = process.env.UV_PROJECT_ENVIRONMENT;
  if (!environment) {
    throw new Error("fake uv sync requires UV_PROJECT_ENVIRONMENT");
  }
  if (process.env.FAKE_UV_SYNC_LOG) {
    appendFileSync(process.env.FAKE_UV_SYNC_LOG, `${process.pid}\n`, "utf8");
  }
  const delay = Number(process.env.FAKE_UV_SYNC_DELAY_MS ?? "0");
  if (Number.isFinite(delay) && delay > 0) {
    await new Promise((resolve) => setTimeout(resolve, delay));
  }
  mkdirSync(environment, { recursive: true });
  writeFileSync(path.join(environment, "pyvenv.cfg"), "fake uv environment\n");
  process.exit(0);
}

if (mode === "inspect") {
  process.stdout.write(
    `${JSON.stringify({
      argv: process.argv.slice(2),
      cwd: process.cwd(),
      projectEnvironment: process.env.UV_PROJECT_ENVIRONMENT ?? null,
      tinyicConfig: process.env.TINYIC_CONFIG ?? null,
    })}\n`,
  );
  process.exit(Number(process.env.FAKE_UV_EXIT ?? "0"));
}

if (mode === "streams") {
  let input = "";
  process.stdin.setEncoding("utf8");
  for await (const chunk of process.stdin) {
    input += chunk;
  }
  if (process.env.FAKE_UV_STDIN_FILE) {
    writeFileSync(process.env.FAKE_UV_STDIN_FILE, input, "utf8");
  }
  process.stdout.write(process.env.FAKE_UV_STDOUT ?? "");
  process.stderr.write(process.env.FAKE_UV_STDERR ?? "");
  process.exit(Number(process.env.FAKE_UV_EXIT ?? "0"));
}

if (mode === "signal") {
  const readyFile = process.env.FAKE_UV_READY_FILE;
  const signalFile = process.env.FAKE_UV_SIGNAL_FILE;
  if (!readyFile || !signalFile) {
    throw new Error("signal mode requires ready and signal files");
  }
  writeFileSync(readyFile, String(process.pid), "utf8");
  const signals = [["SIGINT", 72], ["SIGTERM", 73]];
  if (process.platform !== "win32") {
    signals.push(["SIGHUP", 74]);
  }
  for (const [signal, code] of signals) {
    process.on(signal, () => {
      appendFileSync(signalFile, `${signal}\n`, "utf8");
      process.exit(code);
    });
  }
  setInterval(() => {}, 1_000);
  readFileSync(readyFile, "utf8");
}
