from __future__ import annotations

import html
import json
import re
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass

from .version import APP_VERSION, RELEASES_LIST_API_URL


@dataclass
class ReleaseNote:
    version: str
    title: str
    url: str | None
    body: str
    body_html: str
    published_at: str | None


@dataclass
class UpdateStatus:
    current_version: str
    latest_version: str | None
    update_available: bool
    release_url: str | None
    zipball_url: str | None
    release_notes: list[ReleaseNote]
    error: str | None = None
    no_releases: bool = False


def check_for_update(timeout: int = 5) -> UpdateStatus:
    request = urllib.request.Request(
        RELEASES_LIST_API_URL,
        headers={
            "Accept": "application/vnd.github.html+json",
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
                zipball_url=None,
                release_notes=[],
                no_releases=True,
            )
        return UpdateStatus(
            current_version=APP_VERSION,
            latest_version=None,
            update_available=False,
            release_url=None,
            zipball_url=None,
            release_notes=[],
            error=str(exc),
        )
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        return UpdateStatus(
            current_version=APP_VERSION,
            latest_version=None,
            update_available=False,
            release_url=None,
            zipball_url=None,
            release_notes=[],
            error=str(exc),
        )

    releases = payload if isinstance(payload, list) else []
    if not releases:
        return UpdateStatus(
            current_version=APP_VERSION,
            latest_version=None,
            update_available=False,
            release_url=None,
            zipball_url=None,
            release_notes=[],
            no_releases=True,
        )

    official_releases = [release for release in releases if not release.get("draft") and not release.get("prerelease")]
    if not official_releases:
        return UpdateStatus(
            current_version=APP_VERSION,
            latest_version=None,
            update_available=False,
            release_url=None,
            zipball_url=None,
            release_notes=[],
            no_releases=True,
        )

    latest_release = official_releases[0]
    latest = _release_version(latest_release)
    newer_releases = [
        release
        for release in official_releases
        if _release_version(release) and _version_key(_release_version(release)) > _version_key(APP_VERSION)
    ]
    return UpdateStatus(
        current_version=APP_VERSION,
        latest_version=latest or None,
        update_available=bool(latest and _version_key(latest) > _version_key(APP_VERSION)),
        release_url=latest_release.get("html_url"),
        zipball_url=latest_release.get("zipball_url"),
        release_notes=[_release_note(release) for release in newer_releases],
    )


def update_status_to_dict(status: UpdateStatus) -> dict[str, object]:
    return {
        **asdict(status),
        "release_notes": [asdict(note) for note in status.release_notes],
    }


def _release_version(release: dict[str, object]) -> str:
    version = str(release.get("tag_name") or release.get("name") or "").strip()
    return version.removeprefix("v")


def _release_note(release: dict[str, object]) -> ReleaseNote:
    version = _release_version(release)
    title = str(release.get("name") or version)
    body = str(release.get("body") or "").strip()
    body_html = str(release.get("body_html") or "").strip()
    if not body_html and body:
        body_html = f"<pre>{html.escape(body)}</pre>"
    return ReleaseNote(
        version=version,
        title=title,
        url=release.get("html_url") if isinstance(release.get("html_url"), str) else None,
        body=body,
        body_html=body_html,
        published_at=release.get("published_at") if isinstance(release.get("published_at"), str) else None,
    )


def _version_key(value: str) -> tuple[int, ...]:
    parts = re.findall(r"\d+", value)
    return tuple(int(part) for part in parts) or (0,)
