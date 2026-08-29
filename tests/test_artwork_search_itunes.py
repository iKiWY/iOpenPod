from __future__ import annotations

import pytest
import requests

from iopenpod.artwork_search import itunes
from iopenpod.artwork_search.errors import ArtworkSearchError
from iopenpod.artwork_search.query import SeedQuery

_ART_100 = "https://is1-ssl.mzstatic.com/image/thumb/Music221/v4/fd/dj.qrikkdwj.jpg/100x100bb.jpg"

_PAYLOAD = {
    "resultCount": 2,
    "results": [
        {
            "collectionName": "Discovery",
            "artistName": "Daft Punk",
            "releaseDate": "2001-03-12T08:00:00Z",
            "artworkUrl100": _ART_100,
        },
        {
            "collectionName": "No Art",
            "artistName": "Nobody",
            "releaseDate": "",
            "artworkUrl100": "",
        },
    ],
}


class _FakeResponse:
    def __init__(self, payload, status: int = 200):
        self._payload = payload
        self.status_code = status

    def raise_for_status(self) -> None:
        return None

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


def test_artwork_url_at_size_replaces_final_segment() -> None:
    assert itunes.artwork_url_at_size(_ART_100, 1200).endswith("/1200x1200bb.jpg")
    assert itunes.artwork_url_at_size(_ART_100, 250).endswith("/250x250bb.jpg")


def test_artwork_url_at_size_leaves_unexpected_shape_alone() -> None:
    other = "https://example.com/cover.jpg"
    assert itunes.artwork_url_at_size(other, 1200) == other


def test_empty_query_makes_no_request(monkeypatch) -> None:
    def explode(*args, **kwargs):
        raise AssertionError("no request should be made")

    monkeypatch.setattr(itunes.requests, "get", explode)
    assert itunes.search(SeedQuery(text="   ")) == []


def test_search_parses_results_and_skips_entries_without_artwork(monkeypatch) -> None:
    monkeypatch.setattr(itunes.requests, "get", lambda *a, **k: _FakeResponse(_PAYLOAD))
    results = itunes.search(SeedQuery(text="daft punk discovery"))
    assert len(results) == 1
    candidate = results[0]
    assert candidate.title == "Discovery"
    assert candidate.artist == "Daft Punk"
    assert candidate.year == "2001"
    assert candidate.source == "iTunes"
    assert candidate.width == 1200
    assert candidate.thumb_url.endswith("/250x250bb.jpg")
    assert candidate.full_url.endswith("/1200x1200bb.jpg")


def test_search_sends_expected_params(monkeypatch) -> None:
    captured: dict = {}

    def fake_get(url, params=None, timeout=None, headers=None):
        captured["url"] = url
        captured["params"] = params
        return _FakeResponse(_PAYLOAD)

    monkeypatch.setattr(itunes.requests, "get", fake_get)
    itunes.search(SeedQuery(text="x"), limit=7)
    assert captured["params"]["entity"] == "album"
    assert captured["params"]["media"] == "music"
    assert captured["params"]["limit"] == 7


def test_network_error_raises_friendly_error(monkeypatch) -> None:
    def fail(*args, **kwargs):
        raise requests.ConnectionError("offline")

    monkeypatch.setattr(itunes.requests, "get", fail)
    with pytest.raises(ArtworkSearchError):
        itunes.search(SeedQuery(text="x"))


def test_bad_json_raises_friendly_error(monkeypatch) -> None:
    monkeypatch.setattr(
        itunes.requests,
        "get",
        lambda *a, **k: _FakeResponse(ValueError("not json")),
    )
    with pytest.raises(ArtworkSearchError):
        itunes.search(SeedQuery(text="x"))
