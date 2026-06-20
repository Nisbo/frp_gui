from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass

from .version import APP_VERSION, RELEASES_API_URL


@dataclass
class UpdateStatus:
    current_version: str
    latest_version: str | None
    update_available: bool
    release_url: str | None
    error: str | None = None
    no_releases: bool = False


def check_for_update(timeout: int = 5) -> UpdateStatus:
    request = urllib.request.Request(
        RELEASES_API_URL,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "frp-gui-update-check",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return UpdateStatus(
                current_version=APP_VERSION,
                latest_version=None,
                update_available=False,
                release_url=None,
                no_releases=True,
            )
        return UpdateStatus(
            current_version=APP_VERSION,
            latest_version=None,
            update_available=False,
            release_url=None,
            error=str(exc),
        )
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        return UpdateStatus(
            current_version=APP_VERSION,
            latest_version=None,
            update_available=False,
            release_url=None,
            error=str(exc),
        )

    latest = str(payload.get("tag_name") or payload.get("name") or "").strip()
    latest = latest.removeprefix("v")
    return UpdateStatus(
        current_version=APP_VERSION,
        latest_version=latest or None,
        update_available=bool(latest and _version_key(latest) > _version_key(APP_VERSION)),
        release_url=payload.get("html_url"),
    )


def _version_key(value: str) -> tuple[int, ...]:
    parts = re.findall(r"\d+", value)
    return tuple(int(part) for part in parts) or (0,)
