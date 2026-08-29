from __future__ import annotations

import io

import pytest
from PIL import Image

from iopenpod.artwork_search import download
from iopenpod.artwork_search.errors import ArtworkSearchError


def _png_bytes(size: int = 32) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (size, size), (10, 20, 30)).save(buffer, "PNG")
    return buffer.getvalue()


class _FakeResponse:
    def __init__(self, body: bytes, content_type: str = "image/png", status: int = 200):
        self.headers = {"Content-Type": content_type}
        self.status_code = status
        self._body = body

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise AssertionError("should not be reached in these tests")

    def iter_content(self, chunk_size: int = 8192):
        for start in range(0, len(self._body), chunk_size):
            yield self._body[start : start + chunk_size]

    def close(self) -> None:
        return None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class _FakeSession:
    def __init__(self, response):
        self._response = response
        self.max_redirects = None
        self.closed = False

    def get(self, url, timeout=None, stream=None, headers=None):
        if isinstance(self._response, Exception):
            raise self._response
        return self._response

    def close(self):
        self.closed = True


def test_rejects_non_http_scheme() -> None:
    with pytest.raises(ArtworkSearchError):
        download.fetch_image("file:///etc/passwd")


def test_rejects_empty_url() -> None:
    with pytest.raises(ArtworkSearchError):
        download.fetch_image("   ")


def test_fetches_a_valid_image(monkeypatch) -> None:
    payload = _png_bytes()
    monkeypatch.setattr(download.requests, "Session", lambda: _FakeSession(_FakeResponse(payload)))
    assert download.fetch_image("https://example.com/a.png") == payload


def test_rejects_non_image_content_type(monkeypatch) -> None:
    monkeypatch.setattr(
        download.requests,
        "Session",
        lambda: _FakeSession(_FakeResponse(b"<html></html>", content_type="text/html")),
    )
    with pytest.raises(ArtworkSearchError) as excinfo:
        download.fetch_image("https://example.com/a.html")
    assert "image" in excinfo.value.info.message.lower()


def test_rejects_body_over_size_cap(monkeypatch) -> None:
    oversized = b"\x89PNG" + b"0" * (download.MAX_IMAGE_BYTES + 1)
    monkeypatch.setattr(download.requests, "Session", lambda: _FakeSession(_FakeResponse(oversized)))
    with pytest.raises(ArtworkSearchError) as excinfo:
        download.fetch_image("https://example.com/big.png")
    assert "too large" in excinfo.value.info.title.lower()


def test_rejects_body_that_is_not_a_real_image(monkeypatch) -> None:
    monkeypatch.setattr(
        download.requests,
        "Session",
        lambda: _FakeSession(_FakeResponse(b"not an image at all")),
    )
    with pytest.raises(ArtworkSearchError):
        download.fetch_image("https://example.com/fake.png")


def test_user_agent_has_name_version_and_contact_url() -> None:
    agent = download.user_agent()
    assert agent.startswith("iOpenPod/")
    assert "github.com/TheRealSavi/iOpenPod" in agent


def test_save_temp_image_writes_recognisable_temp_file(tmp_path) -> None:
    import os

    path = download.save_temp_image(_png_bytes())
    try:
        assert os.path.basename(path).startswith("iopenpod-artwork-")
        with Image.open(path) as image:
            assert image.size == (32, 32)
    finally:
        os.remove(path)


def test_session_redirect_cap_is_five(monkeypatch) -> None:
    session = _FakeSession(_FakeResponse(_png_bytes()))
    monkeypatch.setattr(download.requests, "Session", lambda: session)
    download.fetch_image("https://example.com/a.png")
    assert session.max_redirects == download.MAX_REDIRECTS
    assert download.MAX_REDIRECTS == 5
