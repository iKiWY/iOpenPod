from __future__ import annotations

import pytest
import requests

from iopenpod.artwork_search import coverart
from iopenpod.artwork_search.errors import ArtworkSearchError
from iopenpod.artwork_search.query import SeedQuery

_MBID = "48117b90-a16e-34ca-a514-19c702df1158"

_MB_PAYLOAD = {
    "count": 1,
    "release-groups": [
        {
            "id": _MBID,
            "title": "Discovery",
            "score": 100,
            "primary-type": "Album",
            "first-release-date": "2001-02-26",
            "artist-credit": [{"name": "Daft Punk"}],
        }
    ],
}

_CAA_PAYLOAD = {
    "images": [
        {
            "front": False,
            "image": "https://coverartarchive.org/release/x/1.jpg",
            "thumbnails": {"250": "https://caa/1-250.jpg", "1200": "https://caa/1-1200.jpg"},
        },
        {
            "front": True,
            "image": "https://coverartarchive.org/release/x/2.png",
            "thumbnails": {
                "250": "https://caa/2-250.jpg",
                "500": "https://caa/2-500.jpg",
                "1200": "https://caa/2-1200.jpg",
            },
        },
    ]
}


class _FakeResponse:
    def __init__(self, payload, status: int = 200):
        self._payload = payload
        self.status_code = status

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            error = requests.HTTPError(f"HTTP {self.status_code}")
            error.response = self  # type: ignore[attr-defined]
            raise error

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


@pytest.fixture(autouse=True)
def _no_rate_limit_sleep(monkeypatch):
    """Keep the 1 req/sec guard from making the suite slow."""
    monkeypatch.setattr(coverart.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(coverart, "_last_mb_request", 0.0, raising=False)


def _router(mb=_MB_PAYLOAD, caa=_CAA_PAYLOAD, caa_status: int = 200):
    def fake_get(url, params=None, timeout=None, headers=None):
        if "musicbrainz.org" in url:
            return _FakeResponse(mb)
        return _FakeResponse(caa, status=caa_status)

    return fake_get


def test_empty_query_makes_no_request(monkeypatch) -> None:
    def explode(*args, **kwargs):
        raise AssertionError("no request should be made")

    monkeypatch.setattr(coverart.requests, "get", explode)
    assert coverart.search(SeedQuery(text="  ")) == []


def test_search_returns_front_cover_only(monkeypatch) -> None:
    monkeypatch.setattr(coverart.requests, "get", _router())
    results = coverart.search(SeedQuery(text="daft punk discovery"))
    assert len(results) == 1
    candidate = results[0]
    assert candidate.title == "Discovery"
    assert candidate.artist == "Daft Punk"
    assert candidate.year == "2001"
    assert candidate.source == "Cover Art Archive"
    assert candidate.thumb_url == "https://caa/2-250.jpg"
    assert candidate.full_url == "https://caa/2-1200.jpg"


def test_full_url_falls_back_through_thumbnail_sizes(monkeypatch) -> None:
    caa = {
        "images": [
            {
                "front": True,
                "image": "https://caa/orig.png",
                "thumbnails": {"500": "https://caa/x-500.jpg"},
            }
        ]
    }
    monkeypatch.setattr(coverart.requests, "get", _router(caa=caa))
    candidate = coverart.search(SeedQuery(text="x"))[0]
    assert candidate.full_url == "https://caa/x-500.jpg"
    assert candidate.thumb_url == "https://caa/x-500.jpg"


def test_full_url_falls_back_to_original_image(monkeypatch) -> None:
    caa = {"images": [{"front": True, "image": "https://caa/orig.png", "thumbnails": {}}]}
    monkeypatch.setattr(coverart.requests, "get", _router(caa=caa))
    candidate = coverart.search(SeedQuery(text="x"))[0]
    assert candidate.full_url == "https://caa/orig.png"


def test_caa_404_is_a_skip_not_an_error(monkeypatch) -> None:
    monkeypatch.setattr(coverart.requests, "get", _router(caa={}, caa_status=404))
    assert coverart.search(SeedQuery(text="x")) == []


def test_release_group_with_no_front_image_is_skipped(monkeypatch) -> None:
    caa = {"images": [{"front": False, "image": "https://caa/back.jpg", "thumbnails": {}}]}
    monkeypatch.setattr(coverart.requests, "get", _router(caa=caa))
    assert coverart.search(SeedQuery(text="x")) == []


def test_musicbrainz_busy_body_raises_friendly_error(monkeypatch) -> None:
    busy = {"error": "The MusicBrainz web server is currently busy. Please try again later."}
    monkeypatch.setattr(coverart.requests, "get", _router(mb=busy))
    with pytest.raises(ArtworkSearchError) as excinfo:
        coverart.search(SeedQuery(text="x"))
    assert "busy" in excinfo.value.info.message.lower()


def test_network_error_raises_friendly_error(monkeypatch) -> None:
    def fail(*args, **kwargs):
        raise requests.ConnectionError("offline")

    monkeypatch.setattr(coverart.requests, "get", fail)
    with pytest.raises(ArtworkSearchError):
        coverart.search(SeedQuery(text="x"))


def test_musicbrainz_request_uses_fielded_query_and_user_agent(monkeypatch) -> None:
    captured: dict = {}

    def fake_get(url, params=None, timeout=None, headers=None):
        if "musicbrainz.org" in url:
            captured["params"] = params
            captured["headers"] = headers
            return _FakeResponse(_MB_PAYLOAD)
        return _FakeResponse(_CAA_PAYLOAD)

    monkeypatch.setattr(coverart.requests, "get", fake_get)
    coverart.search(SeedQuery(text="x", artist="Daft Punk", album="Discovery"))
    assert captured["params"]["query"] == 'releasegroup:"Discovery" AND artist:"Daft Punk"'
    assert captured["params"]["fmt"] == "json"
    assert captured["headers"]["User-Agent"].startswith("iOpenPod/")
