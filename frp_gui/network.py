from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass
class NetworkConfig:
    internal_host: str
    internal_port: int
    public_port: int
    server_name: str
    nginx_site_path: Path


def render_nginx_config(config: NetworkConfig) -> str:
    return f"""server {{
    listen {config.public_port};
    server_name {config.server_name};

    location / {{
        proxy_pass http://{config.internal_host}:{config.internal_port};
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }}
}}
"""


def check_network(config: NetworkConfig) -> list[dict[str, str]]:
    diagnostics: list[dict[str, str]] = []
    diagnostics.extend(_port_checks(config))

    nginx = shutil.which("nginx")
    if nginx:
        diagnostics.append({"label": "nginx binary", "status": "ok", "message": f"Found: {nginx}"})
        version = subprocess.run([nginx, "-v"], check=False, capture_output=True, text=True)
        version_text = (version.stderr or version.stdout).strip()
        if version_text:
            diagnostics.append({"label": "nginx version", "status": "ok", "message": version_text})
    else:
        diagnostics.append({"label": "nginx binary", "status": "warning", "message": "nginx is not available in this environment."})

    if shutil.which("systemctl"):
        diagnostics.append({"label": "systemctl", "status": "ok", "message": "systemctl is available."})
    else:
        diagnostics.append({"label": "systemctl", "status": "warning", "message": "systemctl is not available in this environment."})

    return diagnostics


def write_nginx_config(config: NetworkConfig) -> Path:
    target = config.nginx_site_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render_nginx_config(config), encoding="utf-8")
    return target


def test_nginx_config() -> tuple[bool, str]:
    nginx = shutil.which("nginx")
    if not nginx:
        return False, "nginx is not available in this environment."
    result = subprocess.run([nginx, "-t"], check=False, capture_output=True, text=True)
    output = (result.stdout + "\n" + result.stderr).strip()
    return result.returncode == 0, output


def _port_checks(config: NetworkConfig) -> list[dict[str, str]]:
    diagnostics: list[dict[str, str]] = []
    for label, port in (("Internal port", config.internal_port), ("Public port", config.public_port)):
        if 1 <= port <= 65535:
            diagnostics.append({"label": label, "status": "ok", "message": f"{port} is a valid TCP port."})
        else:
            diagnostics.append({"label": label, "status": "error", "message": f"{port} is outside the valid range 1-65535."})

    if config.internal_host in {"0.0.0.0", "::"}:
        diagnostics.append({
            "label": "Internal host",
            "status": "warning",
            "message": "Binding the app directly to all interfaces is not recommended. Use 127.0.0.1 behind nginx.",
        })
    else:
        diagnostics.append({"label": "Internal host", "status": "ok", "message": f"App backend binds to {config.internal_host}."})
    return diagnostics
