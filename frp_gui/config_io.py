from __future__ import annotations

import configparser
import re
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


PROXY_TYPES = {"http", "https", "tcp", "udp", "stcp", "sudp", "xtcp", "tcpmux"}
NAME_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
HOST_RE = re.compile(r"^[A-Za-z0-9_.:-]+$")
SECTION_RE = re.compile(r"^\s*\[(?P<name>[^\]]+)]\s*$")
COMMENTED_SECTION_RE = re.compile(r"^\s*[#;]\s*\[(?P<name>[^\]]+)]\s*$")
COMMENTED_KEY_RE = re.compile(r"^\s*[#;]\s*(?P<key>[A-Za-z0-9_.-]+)\s*=\s*(?P<value>.*)$")


@dataclass
class FrpConfig:
    common: dict[str, str]
    proxies: list[dict[str, str]]


def _parser() -> configparser.ConfigParser:
    parser = configparser.ConfigParser(
        interpolation=None,
        delimiters=("=",),
        comment_prefixes=("#", ";"),
        strict=False,
    )
    parser.optionxform = str
    return parser


def read_ini(path: Path) -> FrpConfig:
    parser = _parser()
    read_files = parser.read(path)
    if not read_files:
        raise FileNotFoundError(f"Config file not found: {path}")

    common = dict(parser.items("common")) if parser.has_section("common") else {}
    proxies: list[dict[str, str]] = []

    for section in parser.sections():
        if section == "common":
            continue
        proxy = {"name": section}
        proxy.update(dict(parser.items(section)))
        proxy["enabled"] = "true"
        proxies.append(proxy)

    proxies.extend(_read_disabled_proxies(path, {proxy["name"] for proxy in proxies}))
    return FrpConfig(common=common, proxies=proxies)


def write_ini(path: Path, config: FrpConfig) -> None:
    parser = _parser()
    parser["common"] = {k: str(v) for k, v in config.common.items() if str(v).strip()}

    for proxy in config.proxies:
        if not _is_enabled(proxy):
            continue
        name = proxy["name"]
        parser[name] = {
            k: str(v)
            for k, v in proxy.items()
            if k not in {"name", "enabled"} and str(v).strip()
        }

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        parser.write(handle, space_around_delimiters=True)
        disabled = [proxy for proxy in config.proxies if not _is_enabled(proxy)]
        if disabled:
            handle.write("\n")
            for proxy in disabled:
                handle.write(f"# [{proxy['name']}]\n")
                for key, value in proxy.items():
                    if key in {"name", "enabled"} or not str(value).strip():
                        continue
                    handle.write(f"# {key} = {value}\n")
                handle.write("\n")


def backup_config(path: Path, backup_dir: Path) -> Path | None:
    if not path.exists():
        return None

    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    backup_path = backup_dir / f"{path.name}.{stamp}.bak"
    shutil.copy2(path, backup_path)
    return backup_path


def validate_common(common: dict[str, str]) -> list[str]:
    errors: list[str] = []
    if not common.get("server_addr", "").strip():
        errors.append("Server address is required.")

    port = common.get("server_port", "").strip()
    if not _valid_port(port):
        errors.append("Server port must be between 1 and 65535.")

    tls = common.get("tls_enable", "").strip().lower()
    if tls and tls not in {"true", "false"}:
        errors.append("TLS must be true or false.")

    return errors


def validate_proxy(proxy: dict[str, str], existing_names: set[str] | None = None) -> list[str]:
    errors: list[str] = []
    name = proxy.get("name", "").strip()
    proxy_type = proxy.get("type", "").strip().lower()
    local_ip = proxy.get("local_ip", "").strip()
    local_port = proxy.get("local_port", "").strip()
    domains = proxy.get("custom_domains", "").strip()
    remote_port = proxy.get("remote_port", "").strip()

    if not NAME_RE.match(name):
        errors.append("Name may only contain letters, numbers, dot, underscore and dash.")
    if existing_names is not None and name in existing_names:
        errors.append("Name already exists.")
    if proxy_type not in PROXY_TYPES:
        errors.append("Type is not allowed.")
    if local_ip and not HOST_RE.match(local_ip):
        errors.append("Local IP/host is invalid.")
    if not _valid_port(local_port):
        errors.append("Local port must be between 1 and 65535.")
    if proxy_type in {"http", "https"} and not domains:
        errors.append("HTTP/HTTPS proxies need at least one domain.")
    if proxy_type in {"tcp", "udp"} and not _valid_port(remote_port):
        errors.append("TCP/UDP proxies need a remote port.")

    return errors


def _valid_port(value: str) -> bool:
    try:
        port = int(value)
    except (TypeError, ValueError):
        return False
    return 1 <= port <= 65535


def _read_disabled_proxies(path: Path, active_names: set[str]) -> list[dict[str, str]]:
    disabled: list[dict[str, str]] = []
    current: dict[str, str] | None = None

    for line in path.read_text(encoding="utf-8").splitlines():
        active_section = SECTION_RE.match(line)
        if active_section:
            if current:
                disabled.append(current)
            current = None
            continue

        commented_section = COMMENTED_SECTION_RE.match(line)
        if commented_section:
            if current:
                disabled.append(current)
            name = commented_section.group("name").strip()
            current = None
            if name != "common" and name not in active_names:
                current = {"name": name, "enabled": "false"}
            continue

        if current is None:
            continue

        commented_key = COMMENTED_KEY_RE.match(line)
        if commented_key:
            current[commented_key.group("key")] = commented_key.group("value").strip()
        elif line.strip() and not line.lstrip().startswith(("#", ";")):
            disabled.append(current)
            current = None

    if current:
        disabled.append(current)

    return disabled


def _is_enabled(proxy: dict[str, str]) -> bool:
    return str(proxy.get("enabled", "true")).lower() == "true"
