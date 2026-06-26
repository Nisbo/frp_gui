from __future__ import annotations

import configparser
import ast
import re
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # Python < 3.11 on the local development Mac.
    tomllib = None


PROXY_TYPES = {"http", "https", "tcp", "udp", "stcp", "sudp", "xtcp", "tcpmux"}
NAME_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
HOST_RE = re.compile(r"^[A-Za-z0-9_.:-]+$")
SECTION_RE = re.compile(r"^\s*\[(?P<name>[^\]]+)]\s*$")
COMMENTED_SECTION_RE = re.compile(r"^\s*[#;]\s*\[(?P<name>[^\]]+)]\s*$")
COMMENTED_KEY_RE = re.compile(r"^\s*[#;]\s*(?P<key>[A-Za-z0-9_.-]+)\s*=\s*(?P<value>.*)$")
COMMENTED_TOML_PROXY_RE = re.compile(r"^\s*[#;]\s*\[\[proxies]]\s*$")


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


def read_config(path: Path) -> FrpConfig:
    if path.suffix.lower() == ".toml":
        return read_toml(path)
    return read_ini(path)


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


def write_config(path: Path, config: FrpConfig) -> None:
    if path.suffix.lower() == ".toml":
        write_toml(path, config)
        return
    write_ini(path, config)


def read_toml(path: Path) -> FrpConfig:
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")

    data = _load_toml(path)
    common = _toml_common(data)
    proxies: list[dict[str, str]] = []

    for item in data.get("proxies", []) or []:
        if not isinstance(item, dict):
            continue
        proxy = {
            "name": str(item.get("name", "")).strip(),
            "type": str(item.get("type", "")).strip(),
            "local_ip": str(item.get("localIP", item.get("local_ip", ""))).strip(),
            "local_port": str(item.get("localPort", item.get("local_port", ""))).strip(),
            "custom_domains": _domains_to_string(item.get("customDomains", item.get("custom_domains", ""))),
            "remote_port": str(item.get("remotePort", item.get("remote_port", ""))).strip(),
            "enabled": "true",
        }
        if proxy["name"]:
            proxies.append(proxy)

    proxies.extend(_read_disabled_toml_proxies(path, {proxy["name"] for proxy in proxies}))
    return FrpConfig(common=common, proxies=proxies)


def write_toml(path: Path, config: FrpConfig) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    enabled = [proxy for proxy in config.proxies if _is_enabled(proxy)]
    disabled = [proxy for proxy in config.proxies if not _is_enabled(proxy)]

    lines: list[str] = []
    common = config.common
    if common.get("server_addr", "").strip():
        lines.append(f"serverAddr = {_toml_string(common['server_addr'])}")
    if common.get("server_port", "").strip():
        lines.append(f"serverPort = {int(common['server_port'])}")
    if common.get("token", "").strip():
        lines.extend(["", "[auth]", f"token = {_toml_string(common['token'])}"])
    if common.get("tls_enable", "").strip():
        lines.extend(["", "[transport.tls]", f"enable = {_toml_bool(common['tls_enable'])}"])

    for proxy in enabled:
        lines.extend(["", *list(_toml_proxy_lines(proxy))])

    if disabled:
        lines.extend(["", "# Disabled proxies kept by FRP Gui. They are commented out so frpc cannot start them."])
        for proxy in disabled:
            for line in _toml_proxy_lines(proxy):
                lines.append(f"# {line}" if line else "#")

    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


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


def _load_toml(path: Path) -> dict:
    if tomllib is not None:
        with path.open("rb") as handle:
            return tomllib.load(handle)
    return _load_basic_toml(path)


def _load_basic_toml(path: Path) -> dict:
    data: dict = {}
    current: dict | None = data
    section: list[str] = []

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line:
            continue
        if line == "[[proxies]]":
            proxy: dict = {}
            data.setdefault("proxies", []).append(proxy)
            current = proxy
            section = ["proxies"]
            continue
        if line.startswith("[") and line.endswith("]"):
            section = [part.strip() for part in line.strip("[]").split(".") if part.strip()]
            current = data
            for part in section:
                current = current.setdefault(part, {})
            continue
        key, separator, value = line.partition("=")
        if not separator or current is None:
            continue
        target = current
        key_parts = [part.strip() for part in key.strip().split(".") if part.strip()]
        for part in key_parts[:-1]:
            target = target.setdefault(part, {})
        target[key_parts[-1]] = _parse_basic_toml_value(value.strip())

    return data


def _parse_basic_toml_value(value: str):
    lowered = value.lower()
    if lowered in {"true", "false"}:
        return lowered == "true"
    if value.startswith("[") and value.endswith("]"):
        try:
            return ast.literal_eval(value)
        except (SyntaxError, ValueError):
            return []
    if value.startswith('"') and value.endswith('"'):
        try:
            return ast.literal_eval(value)
        except (SyntaxError, ValueError):
            return value.strip('"')
    try:
        return int(value)
    except ValueError:
        return value


def _toml_common(data: dict) -> dict[str, str]:
    common_section = data.get("common", {}) if isinstance(data.get("common"), dict) else {}
    auth = data.get("auth", {}) if isinstance(data.get("auth"), dict) else {}
    transport = data.get("transport", {}) if isinstance(data.get("transport"), dict) else {}
    tls = transport.get("tls", {}) if isinstance(transport.get("tls"), dict) else {}
    return {
        "server_addr": str(data.get("serverAddr", common_section.get("server_addr", common_section.get("serverAddr", "")))).strip(),
        "server_port": str(data.get("serverPort", common_section.get("server_port", common_section.get("serverPort", "")))).strip(),
        "token": str(auth.get("token", common_section.get("token", ""))).strip(),
        "tls_enable": str(tls.get("enable", common_section.get("tls_enable", ""))).lower().strip(),
    }


def _domains_to_string(value) -> str:
    if isinstance(value, list):
        return ", ".join(str(item).strip() for item in value if str(item).strip())
    return str(value).strip()


def _toml_proxy_lines(proxy: dict[str, str]):
    yield "[[proxies]]"
    yield f"name = {_toml_string(proxy.get('name', ''))}"
    yield f"type = {_toml_string(proxy.get('type', ''))}"
    if proxy.get("local_ip", "").strip():
        yield f"localIP = {_toml_string(proxy['local_ip'])}"
    if proxy.get("local_port", "").strip():
        yield f"localPort = {int(proxy['local_port'])}"
    if proxy.get("custom_domains", "").strip():
        domains = [_toml_string(item.strip()) for item in proxy["custom_domains"].split(",") if item.strip()]
        yield f"customDomains = [{', '.join(domains)}]"
    if proxy.get("remote_port", "").strip():
        yield f"remotePort = {int(proxy['remote_port'])}"


def _toml_string(value: str) -> str:
    escaped = str(value).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _toml_bool(value: str) -> str:
    return "true" if str(value).strip().lower() in {"1", "true", "yes", "on"} else "false"


def _read_disabled_toml_proxies(path: Path, active_names: set[str]) -> list[dict[str, str]]:
    disabled: list[dict[str, str]] = []
    current: dict[str, str] | None = None

    for line in path.read_text(encoding="utf-8").splitlines():
        if COMMENTED_TOML_PROXY_RE.match(line):
            if current:
                disabled.append(current)
            current = {"enabled": "false"}
            continue
        if current is None:
            continue
        key_match = COMMENTED_KEY_RE.match(line)
        if not key_match:
            if line.strip() and not line.lstrip().startswith(("#", ";")):
                if current:
                    disabled.append(current)
                current = None
            continue
        key = key_match.group("key")
        value = str(_parse_basic_toml_value(key_match.group("value").strip()))
        mapped_key = {
            "localIP": "local_ip",
            "localPort": "local_port",
            "customDomains": "custom_domains",
            "remotePort": "remote_port",
        }.get(key, key)
        if mapped_key == "custom_domains":
            value = _domains_to_string(_parse_basic_toml_value(key_match.group("value").strip()))
        current[mapped_key] = value

    if current:
        disabled.append(current)

    return [proxy for proxy in disabled if proxy.get("name") and proxy["name"] not in active_names]


def _is_enabled(proxy: dict[str, str]) -> bool:
    return str(proxy.get("enabled", "true")).lower() == "true"
