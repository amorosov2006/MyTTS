#!/usr/bin/env node
// Runs the mock backend and the Vite dev server together, no extra deps needed.
import { spawn } from "node:child_process";

const procs = [
  spawn("node", ["mock/server.mjs"], { stdio: "inherit" }),
  spawn("npx", ["vite"], { stdio: "inherit" }),
];

function shutdown() {
  for (const p of procs) p.kill();
  process.exit(0);
}

process.on("SIGINT", shutdown);
process.on("SIGTERM", shutdown);
for (const p of procs) p.on("exit", (code) => { if (code) shutdown(); });
