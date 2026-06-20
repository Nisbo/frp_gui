#!/usr/bin/env python3
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path


CONFIG_RE = re.compile(r"\b-c\s+(?P<path>\S+)")


def main() -> None:
    candidates = []
    for service in ("frpc", "frp-client", "frpc.service"):
        unit = service if service.endswith(".service") else f"{service}.service"
        details = inspect_unit(unit)
        if details:
            candidates.append(details)

    if not candidates:
        process = inspect_process()
        if process:
            candidates.append(process)

    result = {
        "frpc_binary": find_binary(),
        "candidates": candidates,
        "recommended": candidates[0] if candidates else None,
    }
    print(json.dumps(result, indent=2))


def inspect_unit(unit: str) -> dict[str, str] | None:
    if not shutil.which("systemctl"):
        return None

    active = run(["systemctl", "is-active", unit])
    if active.returncode != 0 and active.stdout.strip() != "active":
        cat = run(["systemctl", "cat", unit])
        if cat.returncode != 0:
            return None
    else:
        cat = run(["systemctl", "cat", unit])

    text = cat.stdout
    config_path = extract_config(text)
    binary = extract_binary(text)
    return {
        "source": "systemd",
        "service": unit,
        "active": active.stdout.strip() or "unknown",
        "frpc_binary": binary or find_binary(),
        "config_path": config_path or "",
    }


def inspect_process() -> dict[str, str] | None:
    ps = run(["ps", "axo", "command"])
    if ps.returncode != 0:
        return None
    for line in ps.stdout.splitlines():
        if "frpc" in line and " -c " in line:
            return {
                "source": "process",
                "service": "",
                "active": "running",
                "frpc_binary": extract_binary(line) or find_binary(),
                "config_path": extract_config(line) or "",
            }
    return None


def extract_config(text: str) -> str | None:
    match = CONFIG_RE.search(text)
    return match.group("path") if match else None


def extract_binary(text: str) -> str | None:
    for part in text.split():
        if part.endswith("/frpc") or part == "frpc":
            return part
    return None


def find_binary() -> str:
    for path in ("/opt/frp/frpc", shutil.which("frpc")):
        if path and Path(path).exists():
            return str(path)
    return "/opt/frp/frpc"


def run(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, check=False, capture_output=True, text=True)


if __name__ == "__main__":
    main()
