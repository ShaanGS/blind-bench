const get = (id) => document.getElementById(id);
const packetData = JSON.parse(get("evidence").textContent);
const scopeButtons = [...document.querySelectorAll("[data-scope]")];
const boardName = (index) => "Board " + "AB"[index];
let verifiedReports = [];
let activeBoard = 1;
let scopeSelection = "both";

async function sha256(value) {
  if (!globalThis.crypto?.subtle) throw new Error("Open this file in a browser with Web Crypto support.");
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(value));
  return [...new Uint8Array(digest)].map((byte) => byte.toString(16).padStart(2, "0")).join("");
}

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function renderScope() {
  get("scope").innerHTML = scopeSvg(verifiedReports, scopeSelection);
  for (const button of scopeButtons) {
    button.setAttribute("aria-pressed", String(button.dataset.scope === scopeSelection));
  }
}

function renderThread() {
  const { run, csv, receipt } = packetData[activeBoard];
  const report = verifiedReports[activeBoard];
  [...get("boards").children].forEach((button, index) => {
    button.setAttribute("aria-pressed", String(index === activeBoard));
  });
  get("thread-heading").textContent = boardName(activeBoard) + " · Switch thread";
  get("thread-source").textContent = run.run + " · " + report.stages.length + " captured posts";
  const started = report.stages[0].post.posted_at_ms;
  get("thread").replaceChildren(...report.stages.map(({ label, actor, post }) => {
    const details = element("details", "event");
    const summary = element("summary");
    const title = element("span");
    title.append(element("span", "event-label", label), element("span", "event-actor", actor));
    const elapsed = "+" + ((post.posted_at_ms - started) / 1000).toFixed(1) + " s";
    summary.append(title, element("span", "event-time", elapsed));
    const body = element("div", "post", post.body);
    body.append(element("span", "post-id", "Mattermost post " + post.id));
    details.append(summary, body);
    return details;
  }));
  get("checks").replaceChildren(...[
    "DC and ripple match 256 raw samples.",
    "Fixture reveal matches the SHA-256 commitment.",
    "Scope selection and operator approval precede the attachment.",
    report.decision === "hold"
      ? "Release attempt was blocked before the operator recorded HOLD."
      : "The operator recorded RELEASE after the scope result.",
    "Repeat measurement was blocked in the recorded thread.",
    ...(receipt ? ["Decision receipt matches the CSV hash and records Switch event IDs."] : []),
  ].map((check) => element("li", "", check)));
  const download = get("download");
  download.href = "data:text/csv;charset=utf-8," + encodeURIComponent(csv);
  download.download = run.run + "-samples.csv";
  download.hidden = false;
  const receiptLink = get("receipt");
  receiptLink.hidden = !receipt;
  if (receipt) {
    receiptLink.href = "data:application/json;charset=utf-8," + encodeURIComponent(receipt);
    receiptLink.download = run.receipt;
  }
}

async function verify() {
  const status = get("status");
  const button = get("verify");
  button.disabled = true;
  status.className = "status mono";
  status.textContent = "Checking the embedded evidence…";
  try {
    verifiedReports = await Promise.all(packetData.map(({ run, csv, receipt }) => auditRun(run, csv, sha256, receipt)));
    get("lead").textContent = "Both read " + verifiedReports[0].dc.toFixed(3) +
      " V DC. One fails the ripple limit. Compare the scope traces and inspect what happened in chat.";
    if (!get("boards").children.length) {
      get("boards").replaceChildren(...verifiedReports.map((report, index) => {
        const board = element("button", "board " + (index === 0 ? "pass" : "hold"));
        board.type = "button";
        board.append(element("span", "mono tag", boardName(index).toUpperCase()),
          element("span", "number", String(report.ripple)),
          element("span", "unit", "mVpp"), element("span", "mono verdict", report.decision.toUpperCase()));
        board.addEventListener("click", () => {
          activeBoard = index;
          renderThread();
          get("thread-heading").scrollIntoView({ block: "nearest" });
        });
        return board;
      }));
    }
    renderScope();
    renderThread();
    status.textContent = "Both recorded runs verified";
  } catch (error) {
    status.className = "status mono error";
    status.textContent = "Evidence check failed: " + error.message;
  } finally {
    button.disabled = false;
  }
}

for (const button of scopeButtons) {
  button.addEventListener("click", () => {
    if (!verifiedReports.length) return;
    scopeSelection = button.dataset.scope;
    renderScope();
  });
}
get("verify").addEventListener("click", verify);
await verify();
