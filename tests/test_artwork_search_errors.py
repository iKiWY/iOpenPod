from __future__ import annotations

import dataclasses

import pytest
import requests

from iopenpod.artwork_search.errors import (
    ArtworkErrorInfo,
    ArtworkSearchError,
    describe_artwork_error,
)
from iopenpod.artwork_search.models import ArtworkCandidate


def test_candidate_defaults() -> None:
    candidate = ArtworkCandidate(
        title="Discovery",
        artist="Daft Punk",
        source="iTunes",
        thumb_url="https://example.com/t.jpg",
        full_url="https://example.com/f.jpg",
    )
    assert candidate.year == ""
    assert candidate.width == 0


def test_candidate_is_frozen() -> None:
    candidate = ArtworkCandidate(
        title="Discovery",
        artist="Daft Punk",
        source="iTunes",
        thumb_url="https://example.com/t.jpg",
        full_url="https://example.com/f.jpg",
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        candidate.title = "Homework"  # type: ignore[misc]


def test_describe_timeout() -> None:
    info = describe_artwork_error(requests.Timeout("slow"))
    assert "timed out" in info.title.lower()
    assert info.message


def test_describe_connection_error() -> None:
    info = describe_artwork_error(requests.ConnectionError("offline"))
    assert "connection" in info.title.lower()


def test_describe_http_error_includes_status_code() -> None:
    response = requests.Response()
    response.status_code = 503
    error = requests.HTTPError("server error", response=response)
    info = describe_artwork_error(error)
    assert info.code == "HTTP 503"


def test_describe_passes_through_artwork_search_error() -> None:
    original = ArtworkErrorInfo(title="Custom", message="Custom message", code="X")
    info = describe_artwork_error(ArtworkSearchError(original))
    assert info is original


def test_describe_unknown_error_mentions_action() -> None:
    info = describe_artwork_error(ValueError("boom"), action="download artwork")
    assert "download artwork" in info.message
