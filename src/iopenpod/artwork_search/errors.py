"""Friendly artwork-search network error descriptions."""

from __future__ import annotations

from dataclasses import dataclass

import requests


@dataclass(frozen=True)
class ArtworkErrorInfo:
    title: str
    message: str
    code: str = ""


class ArtworkSearchError(RuntimeError):
    """Network or provider failure with user-facing title/message/code."""

    def __init__(self, info: ArtworkErrorInfo):
        super().__init__(info.message)
        self.info = info


def describe_artwork_error(
    error: BaseException,
    *,
    action: str = "search for artwork",
) -> ArtworkErrorInfo:
    """Return short, user-facing copy for an artwork network failure."""
    if isinstance(error, ArtworkSearchError):
        return error.info

    if isinstance(error, requests.Timeout):
        return ArtworkErrorInfo(
            title="The connection timed out",
            message="The artwork service took too long to answer. Try again in a moment.",
        )

    if isinstance(error, requests.ConnectionError):
        return ArtworkErrorInfo(
            title="No internet connection",
            message="iOpenPod could not reach the artwork service. Check your connection and try again.",
        )

    if isinstance(error, requests.HTTPError):
        response = getattr(error, "response", None)
        status_code = getattr(response, "status_code", None)
        if isinstance(status_code, int):
            code = f"HTTP {status_code}"
            if 400 <= status_code < 500:
                return ArtworkErrorInfo(
                    title="The artwork service rejected the request",
                    message="The service refused this search, or the image has moved. The code below can help identify the issue.",
                    code=code,
                )
            return ArtworkErrorInfo(
                title="The artwork service is having trouble",
                message="The server answered with an error. This usually clears up after a little while.",
                code=code,
            )

    return ArtworkErrorInfo(
        title="Something went wrong",
        message=f"iOpenPod could not {action}. Try again in a moment.",
    )
