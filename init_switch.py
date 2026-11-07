"""Prepare a local Switch and Mattermost stack using the tested upstream release."""

import argparse
import os
from pathlib import Path
from secrets import token_hex
from urllib.request import urlopen


VERSION = "0.31.0"
COMPOSE_URL = (
    f"https://raw.githubusercontent.com/sandbox-quantum/switch/switch-v{VERSION}"
    "/deploy/local/standalone-docker-compose.yml"
)


def initialize(directory):
    directory.mkdir(parents=True, exist_ok=True)
    compose = directory / "compose.yml"
    if not compose.exists():
        with urlopen(COMPOSE_URL, timeout=30) as response:
            contents = response.read()
        with compose.open("xb") as file:
            file.write(contents)

    env = directory / ".env"
    if not env.exists():
        settings = {
            "SWITCH_VERSION": VERSION,
            "SWITCH_BIND_ADDR": "127.0.0.1",
            "DB_NAME": "switch",
            "DB_USER": "switch_app",
            "DB_OWNER_USER": "postgres",
            "GATEWAY_ADMIN_EMAIL": "owner@blindbench.test",
            "GATEWAY_PUBLIC_URL": "http://localhost:8000",
            "FRONTEND_BASE_URL": "http://localhost:3000",
            "GATEWAY_COOKIE_SECURE": "false",
            "TELEMETRY_ENABLED": "false",
            "MATTERMOST_ADMIN_USER": "admin",
            "MATTERMOST_TEAM_NAME": "blindbench",
            "MATTERMOST_USER": "operator",
            "SECRET_KEYS": f"local:{token_hex(32)}",
        }
        for key in (
            "DB_PASSWORD", "DB_OWNER_PASSWORD", "AGENT_REGISTRATION_TOKEN",
            "GATEWAY_ADMIN_PASSWORD", "MATTERMOST_ADMIN_PASSWORD", "MATTERMOST_USER_PASSWORD",
        ):
            settings[key] = "Aq9" + token_hex(32)
        fd = os.open(env, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="ascii") as file:
            file.writelines(f"{key}={value}\n" for key, value in settings.items())
    print(f"Switch {VERSION} configuration is ready in {directory}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, default=Path("runtime/switch"))
    initialize(parser.parse_args().directory)
