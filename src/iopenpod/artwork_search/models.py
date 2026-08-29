"""Data model for a single online artwork search result."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ArtworkCandidate:
    """One cover image offered by a provider.

    ``thumb_url`` is a small image for the result grid; ``full_url`` is the
    high-resolution image downloaded only when the user picks this candidate.
    """

    title: str
    artist: str
    source: str
    thumb_url: str
    full_url: str
    year: str = ""
    width: int = 0

    @property
    def detail_line(self) -> str:
        """Short ``year · source · width`` summary for the result card."""
        parts = [part for part in (self.year, self.source) if part]
        if self.width:
            parts.append(f"{self.width}px")
        return "  ·  ".join(parts)
