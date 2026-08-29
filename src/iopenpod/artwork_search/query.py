"""Build artwork search queries from a track selection."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SeedQuery:
    """A pre-filled search box value, plus its separated parts when known."""

    text: str
    artist: str = ""
    album: str = ""


def _field(track: dict, key: str) -> str:
    return str(track.get(key) or "").strip()


def _shared(tracks: list[dict], key: str) -> str:
    """Return the common value for ``key``, or "" when tracks disagree."""
    values = {_field(track, key) for track in tracks}
    if len(values) != 1:
        return ""
    value = values.pop()
    return value


def seed_query_from_tracks(tracks: list[dict]) -> SeedQuery:
    """Pre-fill the search box from the selected tracks.

    All tracks share Album Artist (or Artist) and Album -> "{artist} {album}",
        fielded: both artist and album are known, so
        ``lucene_release_group_query`` can build ``releasegroup:… AND
        artist:…``.
    Album known but no resolvable artist (single track with no artist, or
        multiple tracks that share an Album but disagree on artist) ->
        the album name alone, still fielded on album (searching the album is
        strictly better than an empty box or a bare track title).
    Single track, no shared album, with a title -> "{artist} {title}" or the
        bare title. This is always sent as free text, never fielded:
        ``lucene_release_group_query`` only fields a query when both artist
        and album are set, and a track title is not a release-group title.
    Anything else -> empty.
    """
    if not tracks:
        return SeedQuery(text="")

    album = _shared(tracks, "Album")
    artist = _shared(tracks, "Album Artist") or _shared(tracks, "Artist")

    if album and artist:
        return SeedQuery(text=f"{artist} {album}", artist=artist, album=album)

    if album:
        return SeedQuery(text=album, album=album)

    if len(tracks) == 1:
        title = _field(tracks[0], "Title")
        single_artist = _field(tracks[0], "Album Artist") or _field(tracks[0], "Artist")
        if single_artist and title:
            return SeedQuery(text=f"{single_artist} {title}", artist=single_artist)
        if title:
            return SeedQuery(text=title)

    return SeedQuery(text="")


def escape_lucene(value: str) -> str:
    """Escape the characters that break a quoted Lucene phrase."""
    return value.replace("\\", "\\\\").replace('"', '\\"')


def lucene_release_group_query(seed: SeedQuery) -> str:
    """Build the MusicBrainz release-group query.

    A fielded query is dramatically more relevant than free text, so use one
    whenever the artist and album are separable.
    """
    if seed.artist and seed.album:
        return f'releasegroup:"{escape_lucene(seed.album)}" AND artist:"{escape_lucene(seed.artist)}"'
    return seed.text
