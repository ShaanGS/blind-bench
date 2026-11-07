"""Minimal client for a Switch agent scoped to one room."""

import asyncio
import json
from urllib.parse import quote, urlencode
from uuid import uuid4

import httpx
from websockets.asyncio.client import connect


class Switch:
    def __init__(self, base, agent, room=None):
        self.base = base.rstrip("/")
        self.path = f"/agents/{quote(agent['id'], safe='')}"
        self.room = room
        self.connection = str(uuid4())
        self.headers = {"Authorization": f"Bearer {agent['api_key']}"}
        self.http = httpx.AsyncClient(base_url=self.base, headers=self.headers, timeout=25)

    async def op(self, operation, *, bound=True, **arguments):
        headers = {"X-Switch-Connection-Id": self.connection} if bound else {}
        response = await self.http.post(f"{self.path}/ops/{operation}", json=arguments, headers=headers)
        response.raise_for_status()
        return response.json()["result"]

    async def say(self, thread, body, target=None):
        if target is None:
            return await self.op("post_message", body=body, thread_id=thread)
        result = await self.op("send_targeted_message", body=body, thread_id=thread, target_names=[target])
        print(f"Handoff to {target}: {result['target_statuses']}", flush=True)
        return result

    async def attach(self, thread, caption, files):
        response = await self.http.post(
            f"{self.path}/rooms/{quote(self.room, safe='')}/media",
            data={"thread_id": thread, "caption": caption},
            files=[("files", attachment) for attachment in files],
        )
        response.raise_for_status()
        return response.json()

    async def listen(self, handle, ready):
        query = urlencode({
            "connection_id": self.connection,
            "rooms": self.room,
            "filter": "addressed",
            "start_from": "head",
        })
        ws_base = self.base.replace("https://", "wss://", 1).replace("http://", "ws://", 1)
        inbox = asyncio.Queue(maxsize=100)
        cursor = 0

        async def receive(socket):
            nonlocal cursor
            async for raw in socket:
                frame = json.loads(raw)
                event = frame.get("event")
                data = frame.get("data") or {}
                if event == "connection_state":
                    cursor = data.get("cursor") or 0
                    await self.op("connect_to_room", room_id=self.room)
                    await ready()
                elif frame.get("type") == "ping" or event == "ping":
                    await socket.send(json.dumps({"type": "pong", "cursor": cursor}))
                elif event == "message" and data.get("room_id") == self.room:
                    payload = data["payload"]
                    if payload["addressed"]:
                        inbox.put_nowait((payload, frame.get("id") or cursor))
                elif event in {"gap", "evicted", "agent.gap", "error"}:
                    raise RuntimeError(f"Switch stream reported {event}; restart this run")
            raise RuntimeError("Switch disconnected; restart with a fresh run ID")

        async def work():
            while True:
                payload, sequence = await inbox.get()
                await handle(payload)
                cursor_update(sequence)

        def cursor_update(sequence):
            nonlocal cursor
            cursor = max(int(cursor), int(sequence))

        try:
            async with connect(f"{ws_base}{self.path}/connection/ws?{query}", additional_headers=self.headers,
                               proxy=None, open_timeout=20) as socket:
                async with asyncio.TaskGroup() as group:
                    group.create_task(receive(socket))
                    group.create_task(work())
        finally:
            await self.http.aclose()
