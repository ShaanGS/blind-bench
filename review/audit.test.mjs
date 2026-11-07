import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readFile } from "node:fs/promises";
import test from "node:test";
import { auditRun, measureCsv } from "./audit.mjs";

const read = (name) => readFile(new URL("../evidence/" + name, import.meta.url), "utf8");
const hash = (value) => createHash("sha256").update(value).digest("hex");
const runs = JSON.parse(await read("rehearsal.json"));
const release = runs.find((run) => run.decision === "release");
const hold = runs.find((run) => run.decision === "hold");
const releaseCsv = await read(release.run + "-samples.csv");
const holdCsv = await read(hold.run + "-samples.csv");

test("audits both recorded Switch runs from their actual samples", async () => {
  const a = await auditRun(release, releaseCsv, hash);
  const b = await auditRun(hold, holdCsv, hash);
  assert.equal(a.dc, b.dc);
  assert.equal(a.ripple, 39.6);
  assert.equal(b.ripple, 132);
  assert.ok(b.stages.some((stage) => stage.label === "Block release"));
});

test("rejects CSV edits that change the result", async () => {
  const edited = releaseCsv.replace("4.958000000", "5.058000000");
  await assert.rejects(auditRun(release, edited, hash), /disagree with the recorded measurements/);
});

test("rejects measurements without a prior operator approval in the thread", async () => {
  const edited = structuredClone(hold);
  const approval = edited.messages.find((post) => post.body === "@bb-bench run " + hold.run);
  approval.author = "Switch bridge";
  await assert.rejects(auditRun(edited, holdCsv, hash), /Missing or out of order/);
});

test("rejects a mismatched fixture reveal", async () => {
  const edited = structuredClone(hold);
  const reveal = edited.messages.find((post) => post.body.includes("Fixture reveal"));
  reveal.body = reveal.body.replace(/nonce \x60[0-9a-f]{32}\x60/, "nonce \x60" + "0".repeat(32) + "\x60");
  await assert.rejects(auditRun(edited, holdCsv, hash), /does not match the original commitment/);
});

test("rejects a release verdict for the failing waveform", async () => {
  await assert.rejects(auditRun({ ...hold, decision: "release" }, holdCsv, hash), /release limits/);
});

test("the 50 mVpp limit is strict at the boundary", () => {
  const rows = Array.from({ length: 256 }, (_, i) => i.toFixed(6) + "," +
    (i % 2 ? "4.975000000" : "4.925000000"));
  const exact = measureCsv(["time_us,voltage_v", ...rows].join("\n"));
  assert.equal(exact.ripple, 50);
  assert.equal(exact.decision, "hold");
});
