"""iTunes Search API artwork provider. No authentication required."""

from __future__ import annotations

import logging
import re

import requests

from .download import HTTP_TIMEOUT, user_agent
from .errors import ArtworkErrorInfo, ArtworkSearchError
from .models import ArtworkCandidate
from .query import SeedQuery

log = logging.getLogger(__name__)

SOURCE_NAME = "iTunes"

_SEARCH_URL = "https://itunes.apple.com/search"
_THUMB_SIZE = 250
_FULL_SIZE = 1200

# Matches the trailing "/100x100bb.jpg" size segment of an mzstatic artwork URL.
_SIZE_SEGMENT = re.compile(r"/\d+x\d+bb\.(jpg|png)$", re.IGNORECASE)


def artwork_url_at_size(url: str, size: int) -> str:
    """Rewrite an mzstatic artwork URL to request a different resolution.

    The service clamps to the source resolution rather than failing, so asking
    for more pixels than exist is safe. A URL that does not have the expected
    size segment is returned unchanged.
    """
    if not url or not _SIZE_SEGMENT.search(url):
        return url
    return _SIZE_SEGMENT.sub(f"/{size}x{size}bb.jpg", url)


def search(seed: SeedQuery, limit: int = 24) -> list[ArtworkCandidate]:
    """Search the iTunes album catalogue for cover art."""
    term = seed.text.strip()
    if not term:
        return []

    params = {
        "term": term,
        "media": "music",
        "entity": "album",
        "limit": max(1, min(limit, 200)),
        "country": "US",
    }

    try:
        response = requests.get(
            _SEARCH_URL,
            params=params,
            timeout=HTTP_TIMEOUT,
            headers={"User-Agent": user_agent()},
        )
        response.raise_for_status()
        payload = response.json()
    except requests.RequestException as exc:
        log.warning("iTunes artwork search failed: %s", exc)
        raise ArtworkSearchError(
            ArtworkErrorInfo(
                title="iTunes did not answer",
                message="iOpenPod could not reach the iTunes artwork service.",
            )
        ) from exc
    except ValueError as exc:
        log.warning("iTunes artwork search returned unreadable JSON: %s", exc)
        raise ArtworkSearchError(
            ArtworkErrorInfo(
                title="iTunes answered strangely",
                message="The iTunes service answered, but iOpenPod could not read the results.",
            )
        ) from exc

    candidates: list[ArtworkCandidate] = []
    for entry in payload.get("results", []):
        art_url = str(entry.get("artworkUrl100") or "").strip()
        if not art_url:
            continue
        candidates.append(
            ArtworkCandidate(
                title=str(entry.get("collectionName") or "").strip(),
                artist=str(entry.get("artistName") or "").strip(),
                source=SOURCE_NAME,
                thumb_url=artwork_url_at_size(art_url, _THUMB_SIZE),
                full_url=artwork_url_at_size(art_url, _FULL_SIZE),
                year=str(entry.get("releaseDate") or "")[:4],
                width=_FULL_SIZE,
            )
        )
    return candidates
