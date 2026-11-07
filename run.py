"""Run a Blind Bench agent, or privately arm one simulated board."""

import argparse
import asyncio
import json
import os
from pathlib import Path
import re
import shutil
import subprocess

from switch_client import Switch


RUN_ID = r"[a-z0-9][a-z0-9-]{0,31}"
BENCH = "bb-bench"
DESIGN = "bb-design"
LIMIT = 50.0
BASELINE = re.compile(rf"Baseline \[({RUN_ID})\] — 4\.958 V DC at 2 A\. \(Simulated\)")
PLAN = re.compile(rf"Test plan \[({RUN_ID})\] — (scope_ripple|repeat_dc)")
RESULT = re.compile(rf"Scope result \[({RUN_ID})\] — (\d+(?:\.\d+)?) mVpp \(simulated\)\.")


def test_choice():
    command = shutil.which("claude")
    if not command:
        raise RuntimeError("Claude CLI is required for the design agent")
    prompt = (
        "You are reviewing a simulated 5 V board under a 2 A load. "
        "The bench measured 4.958 V DC. DC must be 4.90 to 5.10 V and "
        "peak-to-peak ripple must be below 50 mV. Choose exactly one next test: "
        "repeat_dc or scope_ripple. Output one line only: the exact test name, "
        "a colon, and one short sentence explaining your choice. Do not predict the test result."
    )
    completed = subprocess.run(
        [command, "-p", "--tools", "", "--no-session-persistence", "--model", "haiku", prompt],
        capture_output=True, text=True, encoding="utf-8", timeout=75, check=True,
    )
    match = re.fullmatch(r"(scope_ripple|repeat_dc):\s*([^\r\n]{1,350})", completed.stdout.strip())
    if not match or "@" in match[2]:
        raise ValueError("Design model did not return a valid test plan")
    return match[1], match[2]


class Workflow:
    def __init__(self, role, config, fixtures):
        self.role = role
        self.operator = config["operator_id"]
        self.peer = config["peer_id"]
        self.fixtures = fixtures
        self.runs = {}
        self.switch = Switch(config["api"], config["agent"], config["room"])

    async def ready(self):
        print(f"{self.role} ready", flush=True)

    async def handle(self, message):
        sender = message["sender"]
        if sender not in {self.operator, self.peer}:
            return
        thread = message.get("thread_id") or message["message_id"]
        body = re.sub(r"^@\S+\s+", "", message["body"].strip(), count=1)
        if sender == self.operator:
            await self.operator_message(thread, body)
        elif self.role == BENCH:
            await self.bench_handoff(thread, body)
        else:
            await self.design_handoff(thread, body)

    async def operator_message(self, thread, body):
        start = re.fullmatch(rf"start ({RUN_ID})", body)
        run = re.fullmatch(rf"run ({RUN_ID})", body)
        decide = re.fullmatch(rf"decide ({RUN_ID}) (hold|release)", body)
        if self.role == BENCH and start:
            await self.start(thread, start[1])
        elif self.role == BENCH and run:
            await self.measure(thread, run[1])
        elif self.role == DESIGN and decide:
            await self.decide(thread, decide[1], decide[2])

    async def start(self, thread, run_id):
        from fixture import commitment

        if run_id in self.runs:
            await self.switch.say(thread, "This run ID has already been used.")
            return
        try:
            record = json.loads((self.fixtures / f"{run_id}.json").read_text(encoding="utf-8"))
        except FileNotFoundError:
            await self.switch.say(thread, f"Run {run_id} has no armed fixture.")
            return
        self.runs[run_id] = {"thread": thread, "stage": "baseline", **record}
        await self.switch.say(
            thread,
            f"Baseline [{run_id}] — 4.958 V DC at 2 A. (Simulated)\n"
            f"DC window: 4.90–5.10 V. Ripple limit: < 50 mVpp.\n"
            f"Fixture commitment: `{commitment(run_id, **record)}`. Which test would you run?",
            DESIGN,
        )

    async def bench_handoff(self, thread, body):
        plan = PLAN.match(body)
        if not plan:
            return
        run_id, test = plan.groups()
        state = self.runs.get(run_id)
        if not state or state["thread"] != thread or state["stage"] != "baseline":
            return
        if test != "scope_ripple":
            await self.switch.say(thread, "A second DC reading leaves ripple unverified. No release recommendation.")
            return
        state["stage"] = "planned"
        await self.switch.say(
            thread, f"**Waiting for operator approval.** Reply in this thread with `@{BENCH} run {run_id}`."
        )

    async def measure(self, thread, run_id):
        from fixture import scope

        state = self.runs.get(run_id)
        if not state or state["thread"] != thread or state["stage"] != "planned":
            await self.switch.say(thread, f"Test blocked [{run_id}] — approve from the run thread after the plan appears.")
            return
        ripple, files = await asyncio.to_thread(scope, run_id, state["board"])
        await self.switch.attach(thread, f"Simulated scope capture and raw samples [{run_id}]", files)
        state["stage"] = "measured"
        await self.switch.say(
            thread,
            f"Scope result [{run_id}] — {ripple:g} mVpp (simulated).\n"
            f"Fixture reveal: `{state['board']}` · nonce `{state['nonce']}`. "
            "The capture and raw samples are attached above.",
            DESIGN,
        )

    async def design_handoff(self, thread, body):
        baseline = BASELINE.match(body)
        if baseline:
            run_id = baseline[1]
            if run_id in self.runs:
                return
            try:
                test, reason = await asyncio.to_thread(test_choice)
            except (ValueError, RuntimeError, subprocess.SubprocessError) as error:
                print(f"Design model failed: {type(error).__name__}", flush=True)
                await self.switch.say(thread, f"No design plan [{run_id}] — model unavailable. Start a fresh run.")
                return
            self.runs[run_id] = {"thread": thread, "stage": test}
            await self.switch.say(thread, f"Test plan [{run_id}] — {test}\n{reason}", BENCH)
            return
        result = RESULT.match(body)
        if not result:
            return
        run_id, measurement = result.groups()
        state = self.runs.get(run_id)
        if not state or state["thread"] != thread or state["stage"] != "scope_ripple":
            return
        ripple = float(measurement)
        action = "release" if ripple < LIMIT else "hold"
        state.update(stage="review", action=action, ripple=ripple)
        comparison = "below" if action == "release" else "at or above"
        await self.switch.say(
            thread,
            f"Recommendation [{run_id}] — **{action.upper()}**.\n"
            f"DC passes at 4.958 V. Ripple is **{ripple:g} mVpp**, {comparison} the {LIMIT:g} mVpp limit.\n"
            f"Operator, record your decision here with `@{DESIGN} decide {run_id} {action}`.",
        )

    async def decide(self, thread, run_id, action):
        state = self.runs.get(run_id)
        if not state or state["thread"] != thread or state["stage"] != "review":
            await self.switch.say(thread, f"Decision blocked [{run_id}] — no open recommendation in this thread.")
            return
        if action == "release" and state["action"] == "hold":
            await self.switch.say(thread, f"Release blocked [{run_id}] — the board failed the ripple limit.")
            return
        state["stage"] = "closed"
        await self.switch.say(
            thread, f"Decision recorded [{run_id}] — **{action.upper()}** by the operator.\n"
            f"Simulated evidence: 4.958 V DC · {state['ripple']:g} mVpp · limit < {LIMIT:g} mVpp.",
        )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", type=Path, default=Path(os.getenv("BLIND_BENCH_RUNTIME", "runtime")))
    commands = parser.add_subparsers(dest="command", required=True)
    arm_parser = commands.add_parser("arm")
    arm_parser.add_argument("run_id")
    arm_parser.add_argument("board", choices=("clean", "noisy"))
    agent_parser = commands.add_parser("agent")
    agent_parser.add_argument("role", choices=(BENCH, DESIGN))
    args = parser.parse_args()
    fixtures = args.runtime / "fixtures"
    if args.command == "arm":
        from fixture import arm

        if not re.fullmatch(RUN_ID, args.run_id):
            parser.error("run ID must be 1–32 lowercase letters, digits, or hyphens")
        print(f"Armed {args.run_id} · commitment {arm(fixtures, args.run_id, args.board)}")
        return
    config = json.loads((args.runtime / f"{args.role}.json").read_text(encoding="utf-8"))
    workflow = Workflow(args.role, config, fixtures if args.role == BENCH else None)
    asyncio.run(workflow.switch.listen(workflow.handle, workflow.ready))


if __name__ == "__main__":
    main()
