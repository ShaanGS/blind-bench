"""Exercise both boards through the real Mattermost bridge and save the evidence."""

import argparse
import csv
import io
import json
from pathlib import Path
import re
import time
from uuid import uuid4

import httpx

from fixture import arm, commitment


def rehearse(args):
    env = dict(
        line.split("=", 1) for line in args.env.read_text(encoding="utf-8-sig").splitlines()
        if "=" in line and not line.startswith("#")
    )
    room = json.loads((args.runtime / "mattermost.json").read_text(encoding="utf-8"))
    args.evidence.mkdir(parents=True, exist_ok=True)
    with httpx.Client(base_url=room["base"], timeout=20) as client:
        login = client.post("/api/v4/users/login", json={
            "login_id": env["MATTERMOST_USER"], "password": env["MATTERMOST_USER_PASSWORD"],
        }).raise_for_status()
        client.headers["Authorization"] = f"Bearer {login.headers['Token']}"
        operator = login.json()["id"]

        def post(message, root=""):
            response = client.post("/api/v4/posts", json={
                "channel_id": room["channel"], "root_id": root, "message": message,
            }).raise_for_status()
            return response.json()["id"]

        def posts(root):
            response = client.get(f"/api/v4/posts/{root}/thread").raise_for_status().json()
            return sorted(response["posts"].values(), key=lambda entry: entry["create_at"])

        def wait(root, text):
            deadline = time.monotonic() + 110
            while time.monotonic() < deadline:
                for entry in posts(root):
                    if entry["user_id"] != operator and text in entry["message"]:
                        return entry
                time.sleep(0.8)
            raise TimeoutError(f"Missing {text!r} in {room['base']}/_redirect/pl/{root}")

        records = []
        for board, expected, action in (("clean", 39.6, "release"), ("noisy", 132.0, "hold")):
            run_id = f"board-{uuid4().hex[:6]}"
            digest = arm(args.runtime / "fixtures", run_id, board)
            root = post(f"@bb-bench start {run_id}")
            link = f"{room['base']}/_redirect/pl/{root}"
            print(f"{run_id} started: {link}", flush=True)
            baseline = wait(root, f"Baseline [{run_id}]")
            assert "4.958 V" in baseline["message"] and digest in baseline["message"]
            plan = wait(root, f"Test plan [{run_id}]")
            assert "scope_ripple" in plan["message"]
            wait(root, "Waiting for operator approval")
            assert all(not entry.get("file_ids") for entry in posts(root))

            wrong_thread = post(f"@bb-bench run {run_id}")
            wait(wrong_thread, f"Test blocked [{run_id}]")
            assert all(not entry.get("file_ids") for entry in posts(root))

            post(f"@bb-bench run {run_id}", root)
            capture = wait(root, f"Simulated scope capture and raw samples [{run_id}]")
            assert len(capture.get("file_ids", [])) == 2
            attachments = {}
            for file_id in capture["file_ids"]:
                info = client.get(f"/api/v4/files/{file_id}/info").raise_for_status().json()
                data = client.get(f"/api/v4/files/{file_id}").raise_for_status().content
                name = info["name"]
                assert name in {f"{run_id}-scope.png", f"{run_id}-samples.csv"}
                attachments[Path(name).suffix] = (name, data)
            assert attachments[".png"][1].startswith(b"\x89PNG\r\n\x1a\n")
            samples = [float(row["voltage_v"]) for row in csv.DictReader(
                io.StringIO(attachments[".csv"][1].decode())
            )]
            assert len(samples) == 256
            assert round(sum(samples) / len(samples), 3) == 4.958
            assert round((max(samples) - min(samples)) * 1000, 1) == expected

            result = wait(root, f"Scope result [{run_id}]")
            assert f"{expected:g} mVpp" in result["message"]
            nonce = re.search(r"nonce `([0-9a-f]{32})`", result["message"])
            assert nonce and commitment(run_id, board, nonce[1]) == digest
            assert f"`{board}`" in result["message"]
            verdict = wait(root, f"Recommendation [{run_id}]")
            assert f"**{action.upper()}**" in verdict["message"]
            assert all("Decision recorded" not in entry["message"] for entry in posts(root))

            if board == "noisy":
                post(f"@bb-design decide {run_id} release", root)
                wait(root, f"Release blocked [{run_id}]")
            post(f"@bb-design decide {run_id} {action}", root)
            decision = wait(root, f"Decision recorded [{run_id}]")
            assert f"**{action.upper()}**" in decision["message"]
            post(f"@bb-bench run {run_id}", root)
            wait(root, f"Test blocked [{run_id}]")
            assert sum(bool(entry.get("file_ids")) for entry in posts(root)) == 1

            for name, data in attachments.values():
                (args.evidence / name).write_bytes(data)
            records.append({
                "run": run_id, "fixture": board, "dc_v": 4.958, "ripple_mvpp": expected,
                "decision": action, "mattermost_thread": link, "wrong_thread_blocked": True,
                "duplicate_measurement_blocked": True, "release_blocked": board == "noisy",
                "messages": [
                    {"id": entry["id"], "root_id": entry["root_id"], "body": entry["message"],
                     "attachments": entry.get("file_ids", []), "posted_at_ms": entry["create_at"],
                     "author": "operator" if entry["user_id"] == operator else "Switch bridge"}
                    for entry in posts(root)
                ],
            })
            print(f"{run_id}: {expected:g} mVpp → {action.upper()}; evidence downloaded from Mattermost", flush=True)
            (args.evidence / "rehearsal.json").write_text(
                json.dumps(records, indent=2, ensure_ascii=False), encoding="utf-8"
            )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env", type=Path, required=True)
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, default=Path("evidence"))
    rehearse(parser.parse_args())
