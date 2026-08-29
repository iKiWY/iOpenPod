"""MusicBrainz + Cover Art Archive artwork provider. No authentication required."""

from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import requests

from .download import HTTP_TIMEOUT, user_agent
from .errors import ArtworkErrorInfo, ArtworkSearchError
from .models import ArtworkCandidate
from .query import SeedQuery, lucene_release_group_query

log = logging.getLogger(__name__)

SOURCE_NAME = "Cover Art Archive"

_MB_URL = "https://musicbrainz.org/ws/2/release-group/"
_CAA_URL = "https://coverartarchive.org/release-group"

# MusicBrainz terms of service: at most one request per second, and a
# descriptive User-Agent. Violating either gets the application IP-blocked.
MB_RATE_LIMIT_SECONDS = 1.0

_CAA_WORKERS = 4
_THUMB_KEYS = ("250", "small")
_FULL_KEYS = ("1200", "500", "large")

_rate_lock = threading.Lock()
_last_mb_request = 0.0


def _throttle_musicbrainz() -> None:
    """Block until at least MB_RATE_LIMIT_SECONDS since the last MB request."""
    global _last_mb_request
    with _rate_lock:
        elapsed = time.monotonic() - _last_mb_request
        wait = MB_RATE_LIMIT_SECONDS - elapsed
        if wait > 0:
            time.sleep(wait)
        _last_mb_request = time.monotonic()


def _artist_of(release_group: dict) -> str:
    parts = []
    for credit in release_group.get("artist-credit", []):
        if isinstance(credit, dict):
            parts.append(str(credit.get("name") or ""))
        elif isinstance(credit, str):
            parts.append(credit)
    return "".join(parts).strip()


def _search_release_groups(seed: SeedQuery, limit: int) -> list[dict]:
    params = {
        "query": lucene_release_group_query(seed),
        "fmt": "json",
        "limit": max(1, min(limit, 25)),
    }
    _throttle_musicbrainz()
    try:
        response = requests.get(
            _MB_URL,
            params=params,
            timeout=HTTP_TIMEOUT,
            headers={"User-Agent": user_agent()},
        )
        response.raise_for_status()
        payload = response.json()
    except requests.RequestException as exc:
        log.warning("MusicBrainz search failed: %s", exc)
        raise ArtworkSearchError(
            ArtworkErrorInfo(
                title="MusicBrainz did not answer",
                message="iOpenPod could not reach the MusicBrainz service.",
            )
        ) from exc
    except ValueError as exc:
        log.warning("MusicBrainz returned unreadable JSON: %s", exc)
        raise ArtworkSearchError(
            ArtworkErrorInfo(
                title="MusicBrainz answered strangely",
                message="The MusicBrainz service answered, but iOpenPod could not read the results.",
            )
        ) from exc

    # MusicBrainz reports overload in the body, not the status line.
    if isinstance(payload, dict) and payload.get("error"):
        log.info("MusicBrainz reported an error body: %s", payload.get("error"))
        raise ArtworkSearchError(
            ArtworkErrorInfo(
                title="MusicBrainz is busy",
                message="The MusicBrainz service is busy right now. Try again in a moment.",
            )
        )

    return list(payload.get("release-groups", []))


def _pick(thumbnails: dict, keys: tuple[str, ...]) -> str:
    for key in keys:
        value = str(thumbnails.get(key) or "").strip()
        if value:
            return value
    return ""


def _candidate_for(release_group: dict) -> ArtworkCandidate | None:
    """Look up cover art for one release group, or None when it has none."""
    mbid = str(release_group.get("id") or "").strip()
    if not mbid:
        return None

    try:
        response = requests.get(
            f"{_CAA_URL}/{mbid}",
            timeout=HTTP_TIMEOUT,
            headers={"User-Agent": user_agent()},
        )
        response.raise_for_status()
        payload = response.json()

        for image in payload.get("images", []):
            if not image.get("front"):
                continue
            thumbnails = image.get("thumbnails") or {}
            original = str(image.get("image") or "").strip()
            full_url = _pick(thumbnails, _FULL_KEYS) or original
            thumb_url = _pick(thumbnails, _THUMB_KEYS) or full_url
            if not full_url:
                continue
            return ArtworkCandidate(
                title=str(release_group.get("title") or "").strip(),
                artist=_artist_of(release_group),
                source=SOURCE_NAME,
                thumb_url=thumb_url,
                full_url=full_url,
                year=str(release_group.get("first-release-date") or "")[:4],
            )
        return None
    except requests.HTTPError as exc:
        # 404 with an HTML body is the normal "this release has no art" answer.
        status = getattr(getattr(exc, "response", None), "status_code", None)
        if status == 404:
            return None
        log.debug("Cover Art Archive lookup failed for %s: %s", mbid, exc)
        return None
    except (requests.RequestException, ValueError, AttributeError, TypeError, KeyError) as exc:
        # Covers both transport/JSON failures and an unexpected payload shape
        # (e.g. a non-dict images entry) — either way, skip this one release
        # group without aborting the others.
        log.debug("Cover Art Archive lookup failed for %s: %s", mbid, exc)
        return None


def search(seed: SeedQuery, limit: int = 8) -> list[ArtworkCandidate]:
    """Find cover art via MusicBrainz release groups and the Cover Art Archive."""
    if not seed.text.strip():
        return []

    release_groups = _search_release_groups(seed, limit)
    if not release_groups:
        return []

    # Cover Art Archive is a separate host and is not covered by the
    # MusicBrainz rate limit, so these lookups may run concurrently.
    with ThreadPoolExecutor(max_workers=_CAA_WORKERS) as pool:
        found = list(pool.map(_candidate_for, release_groups))

    return [candidate for candidate in found if candidate is not None]
