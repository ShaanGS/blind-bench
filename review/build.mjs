import { createHash } from "node:crypto";
import { readFile, writeFile } from "node:fs/promises";
import { auditRun } from "./audit.mjs";
import { overviewSvg } from "./plot.mjs";

const here = import.meta.url;
const read = (path) => readFile(new URL(path, here), "utf8");
const normalize = (text) => text.replace(/\r\n/g, "\n");
const check = process.argv.includes("--check");
const runs = JSON.parse(await read("../evidence/rehearsal.json"));
if (runs.length !== 2 || new Set(runs.map((run) => run.run)).size !== 2) {
  throw new Error("The review requires two distinct rehearsal runs");
}
runs.sort((a, b) => b.decision.localeCompare(a.decision));

const packet = [];
const reports = [];
for (const run of runs) {
  if (!/^[a-z0-9-]{1,32}$/.test(run.run)) throw new Error("Invalid run ID");
  const csv = normalize(await read("../evidence/" + run.run + "-samples.csv"));
  const report = await auditRun(run, csv, (value) => createHash("sha256").update(value).digest("hex"));
  packet.push({ run, csv });
  reports.push(report);
  console.log(run.run + "  " + report.dc.toFixed(3) + " V  " + report.ripple + " mVpp  " + report.decision.toUpperCase());
}
if (reports[0].dc !== reports[1].dc || reports[0].decision !== "release" || reports[1].decision !== "hold") {
  throw new Error("Expected the same DC reading with opposite release decisions");
}

const template = normalize(await read("./review.template.html"));
const modules = await Promise.all(["audit.mjs", "plot.mjs", "viewer.js"].map((file) => read("./" + file)));
const html = template
  .replace("<!-- EVIDENCE -->", () => JSON.stringify(packet).replaceAll("<", "\\u003c"))
  .replace("/* REVIEW SCRIPT */", () => modules.map(normalize).join("\n"));

for (const [name, content] of [["../docs/index.html", html], ["scope-comparison.svg", overviewSvg(reports)]]) {
  if (check) {
    if (normalize(await read("./" + name)) !== content) {
      throw new Error(name + " is out of date. Run node review/build.mjs");
    }
  } else {
    await writeFile(new URL(name, here), content);
  }
}
