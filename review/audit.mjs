export const LIMIT_MVPP = 50;
const NANOVOLTS_PER_MV = 1_000_000;
const DC_MIN_NV = 4_900_000_000;
const DC_MAX_NV = 5_100_000_000;

function requireCheck(condition, message) {
  if (!condition) throw new Error(message);
}

export function measureCsv(csv) {
  const [header, ...rows] = csv.trimEnd().split(/\r?\n/);
  requireCheck(header === "time_us,voltage_v" && rows.length === 256, "Expected 256 scope samples");
  let previousTime = -1;
  const samples = rows.map((row) => {
    const match = /^(\d+\.\d{6}),(\d+)\.(\d{9})$/.exec(row);
    requireCheck(match, "Invalid scope sample");
    const time = Number(match[1]);
    const voltage = Number(match[2]) * 1_000_000_000 + Number(match[3]);
    requireCheck(time > previousTime && Number.isSafeInteger(voltage) && voltage <= 10_000_000_000,
      "Samples must have increasing timestamps and valid voltages");
    previousTime = time;
    return [time, voltage];
  });
  const voltages = samples.map((sample) => sample[1]);
  const mean = voltages.reduce((total, value) => total + value, 0) / voltages.length;
  const ripple = Math.max(...voltages) - Math.min(...voltages);
  return {
    dc: Math.round(mean / 1_000_000) / 1_000,
    ripple: ripple / NANOVOLTS_PER_MV,
    decision: mean >= DC_MIN_NV && mean <= DC_MAX_NV && ripple < LIMIT_MVPP * NANOVOLTS_PER_MV
      ? "release" : "hold",
    points: samples.map(([time, voltage]) => [time, (voltage - mean) / NANOVOLTS_PER_MV]),
  };
}

export async function auditRun(run, csv, hash, receiptText = null) {
  const id = run.run;
  const measurements = measureCsv(csv);
  const messages = run.messages;
  const root = messages[0];
  requireCheck(/^[a-z0-9-]{1,32}$/.test(id) && root?.id && root.root_id === "", "Invalid thread root");
  requireCheck(new Set(messages.map((post) => post.id)).size === messages.length, "Duplicate post ID");
  requireCheck(messages.every((post, index) => Number.isSafeInteger(post.posted_at_ms) &&
    (!index || (post.root_id === root.id && post.posted_at_ms >= messages[index - 1].posted_at_ms))),
  "Thread membership or post order is invalid");
  requireCheck(measurements.dc === run.dc_v &&
    Math.round(measurements.ripple * 10) / 10 === run.ripple_mvpp,
  "Raw scope samples disagree with the recorded measurements");
  requireCheck(measurements.decision === run.decision, "Recorded decision disagrees with the release limits");

  const stages = [];
  let cursor = -1;
  function stage(label, actor, text, exact = false) {
    const author = actor === "Operator" ? "operator" : "Switch bridge";
    const index = messages.findIndex((post, i) => i > cursor && post.author === author &&
      (exact ? post.body === text : post.body.startsWith(text)));
    requireCheck(index !== -1, "Missing or out of order: " + label);
    cursor = index;
    const post = messages[index];
    stages.push({ label, actor, post });
    return post;
  }

  stage("Start board review", "Operator", "@bb-bench start " + id, true);
  const baseline = stage("Commit the fixture", "Bench agent",
    "@bb-design Baseline [" + id + "] — " + measurements.dc.toFixed(3) + " V DC at 2 A. (Simulated)");
  stage("Choose the scope test", "Design agent", "@bb-bench Test plan [" + id + "] — scope_ripple\n");
  stage("Ask for approval", "Bench agent", "**Waiting for operator approval.**");
  stage("Approve the measurement", "Operator", "@bb-bench run " + id, true);
  const capture = stage("Attach scope and samples", "Bench agent", "Simulated scope capture and raw samples [" + id + "]");
  requireCheck(capture.attachments.length === 2 &&
    messages.filter((post) => post.attachments.length).length === (run.receipt ? 2 : 1),
  "Unexpected attachments in the decision thread");
  const result = stage("Reveal the fixture", "Bench agent",
    "@bb-design Scope result [" + id + "] — " + run.ripple_mvpp + " mVpp (simulated).");
  const committed = /Fixture commitment: \x60([0-9a-f]{64})\x60/.exec(baseline.body);
  const revealed = /Fixture reveal: \x60(clean|noisy)\x60 · nonce \x60([0-9a-f]{32})\x60/.exec(result.body);
  requireCheck(committed && revealed && revealed[1] === run.fixture, "Missing fixture commitment or reveal");
  requireCheck(committed[1] === await hash([id, revealed[1], revealed[2]].join(":")),
    "Fixture reveal does not match the original commitment");

  const decision = run.decision.toUpperCase();
  stage("Recommend " + decision, "Design agent", "Recommendation [" + id + "] — **" + decision + "**.");
  if (run.decision === "hold") {
    stage("Attempt release", "Operator", "@bb-design decide " + id + " release", true);
    stage("Block release", "Design agent", "Release blocked [" + id + "]");
  }
  stage("Confirm " + decision, "Operator", "@bb-design decide " + id + " " + run.decision, true);
  const recorded = stage("Record " + decision, "Design agent",
    "Decision recorded [" + id + "] — **" + decision + "**");
  stage("Repeat measurement", "Operator", "@bb-bench run " + id, true);
  stage("Block repeat", "Bench agent", "Test blocked [" + id + "]");
  if (run.receipt) {
    requireCheck(run.receipt === id + "-decision.json" && recorded.attachments.length === 1 && receiptText,
      "Decision receipt missing from the recorded post");
    const receipt = JSON.parse(receiptText);
    requireCheck(receipt.version === 1 && receipt.run === id && receipt.simulated === true &&
      receipt.decision === run.decision, "Decision receipt disagrees with the thread");
    const scope = receipt.scope;
    const samplesHash = await hash(csv);
    requireCheck(scope.dc_v === run.dc_v && scope.load_a === 2 &&
      scope.ripple_mvpp === run.ripple_mvpp && scope.limit_mvpp_exclusive === LIMIT_MVPP &&
      scope.samples_sha256 === samplesHash && result.body.includes("Samples SHA-256: " + samplesHash),
    "Decision receipt does not match the scope samples");
    const eventRefs = receipt.switch;
    const refs = ["room", "thread", "scope_approval", "bench_result", "operator_decision"]
      .map((name) => eventRefs[name]);
    requireCheck(refs.every((ref) => typeof ref === "string" && /^\S+$/.test(ref)) &&
      new Set(refs).size === refs.length &&
      result.body.includes("Switch scope approval: " + eventRefs.scope_approval),
    "Decision receipt is missing Switch event references");
    stage("Repeat decision", "Operator", "@bb-design decide " + id + " " + run.decision, true);
    stage("Block duplicate decision", "Design agent", "Decision blocked [" + id + "]");
  }
  requireCheck(stages.length === messages.length, "Unexpected messages in the recorded review");
  return { ...measurements, stages, commitment: committed[1], nonce: revealed[2] };
}
