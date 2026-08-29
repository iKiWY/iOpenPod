"""Guarded image fetching shared by every artwork source."""

from __future__ import annotations

import io
import logging
import os
import tempfile
from urllib.parse import urlparse

import requests
from PIL import Image, UnidentifiedImageError

from iopenpod.infrastructure.version import get_version

from .errors import ArtworkErrorInfo, ArtworkSearchError

log = logging.getLogger(__name__)

HTTP_TIMEOUT = 15
MAX_REDIRECTS = 5
MAX_IMAGE_BYTES = 20 * 1024 * 1024
TEMP_PREFIX = "iopenpod-artwork-"
CONTACT_URL = "https://github.com/TheRealSavi/iOpenPod"

_ALLOWED_SCHEMES = frozenset({"http", "https"})


def user_agent() -> str:
    """MusicBrainz requires a descriptive agent naming the app and a contact URL."""
    return f"iOpenPod/{get_version()} ( {CONTACT_URL} )"


def _fail(title: str, message: str) -> ArtworkSearchError:
    return ArtworkSearchError(ArtworkErrorInfo(title=title, message=message))


def fetch_image(url: str) -> bytes:
    """Download an image, refusing anything that violates a guard.

    Raises ArtworkSearchError with user-facing copy on every rejection.
    """
    url = (url or "").strip()
    if not url:
        raise _fail("No image address", "Enter an image address first.")

    parsed = urlparse(url)
    if parsed.scheme.lower() not in _ALLOWED_SCHEMES:
        raise _fail(
            "That address will not work",
            "Artwork can only be loaded from a http:// or https:// address.",
        )

    session = requests.Session()
    session.max_redirects = MAX_REDIRECTS
    try:
        with session.get(
            url,
            timeout=HTTP_TIMEOUT,
            stream=True,
            headers={"User-Agent": user_agent()},
        ) as response:
            response.raise_for_status()
            content_type = str(response.headers.get("Content-Type", "")).lower()
            if not content_type.startswith("image/"):
                raise _fail(
                    "That address is not an image",
                    f"The server answered with {content_type or 'an unknown type'} instead of an image.",
                )

            chunks: list[bytes] = []
            total = 0
            for chunk in response.iter_content(chunk_size=64 * 1024):
                if not chunk:
                    continue
                total += len(chunk)
                if total > MAX_IMAGE_BYTES:
                    raise _fail(
                        "That image is too large",
                        f"That image is too large. Artwork must be smaller than {MAX_IMAGE_BYTES // (1024 * 1024)} MB.",
                    )
                chunks.append(chunk)
    except requests.TooManyRedirects as exc:
        raise _fail(
            "That address redirected too many times",
            "The image address kept forwarding somewhere else. Try a direct link to the image.",
        ) from exc
    finally:
        session.close()

    data = b"".join(chunks)
    try:
        with Image.open(io.BytesIO(data)) as probe:
            probe.verify()
    except (OSError, UnidentifiedImageError) as exc:
        log.debug("Rejected artwork payload from %s", url, exc_info=True)
        raise _fail(
            "That image could not be read",
            "The download finished, but the file was not a readable image.",
        ) from exc

    return data


def save_temp_image(data: bytes) -> str:
    """Write image bytes to a temp file the app knows how to clean up."""
    fd, path = tempfile.mkstemp(prefix=TEMP_PREFIX, suffix=".png")
    os.close(fd)
    try:
        with Image.open(io.BytesIO(data)) as image:
            image.convert("RGB").save(path, "PNG")
    except Exception:
        try:
            os.remove(path)
        except OSError:
            pass
        raise
    return path
