"""Register the two agents and provision a private Mattermost-backed Switch room."""

import argparse
import asyncio
import json
from pathlib import Path
from urllib.parse import quote

import httpx

from switch_client import Switch


def check(response):
    if response.is_error:
        detail = response.json().get("detail", response.reason_phrase)
        raise RuntimeError(f"{response.request.method} {response.request.url.path}: {response.status_code} {detail}")
    return response.json()


def save(path, data):
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


async def setup(args):
    env = dict(
        line.split("=", 1) for line in args.env.read_text(encoding="utf-8-sig").splitlines()
        if "=" in line and not line.startswith("#")
    )
    args.runtime.mkdir(parents=True, exist_ok=True)
    async with httpx.AsyncClient(timeout=45) as http:
        agents = {}
        for name, description in (
            ("bb-bench", "Runs approved measurements on a privately armed simulated fixture."),
            ("bb-design", "Chooses the next test from evidence and makes a release recommendation."),
        ):
            path = args.runtime / f"{name}-credentials.json"
            if path.exists():
                agents[name] = json.loads(path.read_text(encoding="utf-8"))
                continue
            profile = {
                "connection_model": "session_addressable", "message_exchange": True,
                "pre_invocation_mediation": [], "post_invocation_mediation": [], "event_reporting": [],
            }
            agents[name] = check(await http.post(
                f"{args.api}/agents",
                headers={"Authorization": f"Bearer {env['AGENT_REGISTRATION_TOKEN']}"},
                json={"name": name, "description": description, "connector_type": "blind-bench",
                      "integration_profile": profile},
            ))
            save(path, agents[name])

        bench = Switch(args.api, agents["bb-bench"])
        design = Switch(args.api, agents["bb-design"])
        try:
            bridges = await bench.op("list_bridges", bound=False)
            bridge = next((entry for entry in bridges if entry["type"] == "mattermost"
                           and entry["status"] == "active"), None)
            if bridge is None:
                raise RuntimeError("No active Mattermost bridge. Check Switch deployment setup.")
            await bench.op("update_agent_detail", bound=False, agent_id=agents["bb-design"]["id"], addressing="anyone")
            await design.op("update_agent_detail", bound=False, agent_id=agents["bb-bench"]["id"], addressing="anyone")
            room_path = args.runtime / "room.json"
            if room_path.exists():
                room = json.loads(room_path.read_text(encoding="utf-8"))
            else:
                room = await bench.op(
                    "create_room", bound=False, name="Blind Bench",
                    description="Two simulated boards. Same DC reading. One passes release review.",
                    agent_names=list(agents), user_names=[env["MATTERMOST_USER"]],
                    channel_type="channel_private", bridge_id=bridge["id"],
                )
                save(room_path, room)
        finally:
            await asyncio.gather(bench.http.aclose(), design.http.aclose())

        room_id = room.get("room_id") or room["id"]
        roles = ("bb-bench", "bb-design")
        if not all((args.runtime / f"{role}.json").exists() for role in roles):
            # Switch's stream uses transport identities. The participant
            # directory gives registry IDs, which differ from message senders.
            name = env["MATTERMOST_USER"]
            expected = {
                "bb-bench": "@switch-agent-",
                "bb-design": "@switch-agent-",
                name: f"@switch-mattermost-{bridge['id']}-",
            }
            history_url = (
                f"{args.api}/agents/{quote(agents['bb-bench']['id'], safe='')}"
                f"/rooms/{quote(room_id, safe='')}/history"
            )
            auth = {"Authorization": f"Bearer {agents['bb-bench']['api_key']}"}
            for _ in range(12):
                history = check(await http.get(history_url, headers=auth))["events"]
                identities = {}
                for sender_name, prefix in expected.items():
                    matches = {
                        event["sender"] for event in history
                        if event["sender_name"] == sender_name and event["sender"].startswith(prefix)
                    }
                    if len(matches) == 1:
                        identities[sender_name] = matches.pop()
                if len(identities) == len(expected):
                    break
                await asyncio.sleep(1)
            else:
                raise RuntimeError("Could not resolve exact transport identities from the new room's history")
            for role, peer in (roles, roles[::-1]):
                save(args.runtime / f"{role}.json", {
                    "api": args.api, "room": room_id, "operator_id": identities[name],
                    "peer_id": identities[peer], "agent": agents[role],
                })

        login = await http.post(f"{args.mattermost}/api/v4/users/login", json={
            "login_id": env["MATTERMOST_USER"], "password": env["MATTERMOST_USER_PASSWORD"],
        })
        check(login)
        headers = {"Authorization": f"Bearer {login.headers['Token']}"}
        teams = check(await http.get(f"{args.mattermost}/api/v4/users/me/teams", headers=headers))
        team = next(team for team in teams if team["name"] == env["MATTERMOST_TEAM_NAME"])
        channels = check(await http.get(f"{args.mattermost}/api/v4/users/me/teams/{team['id']}/channels", headers=headers))
        channel = next(channel for channel in channels if channel["display_name"] == "Blind Bench")
        link = f"{args.mattermost}/{team['name']}/channels/{channel['name']}"
        save(args.runtime / "mattermost.json", {"base": args.mattermost, "channel": channel["id"], "url": link})
        print(f"Switch room {room_id}\nMattermost room {link}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api", default="http://127.0.0.1:8000")
    parser.add_argument("--mattermost", default="http://localhost:8065")
    parser.add_argument("--env", type=Path, required=True, help="Existing official Switch compose environment")
    parser.add_argument("--runtime", type=Path, required=True, help="Local directory for agent keys and fixtures")
    asyncio.run(setup(parser.parse_args()))
