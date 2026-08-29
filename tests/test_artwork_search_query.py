from __future__ import annotations

from iopenpod.artwork_search.query import (
    SeedQuery,
    escape_lucene,
    lucene_release_group_query,
    seed_query_from_tracks,
)


def _track(**kwargs: str) -> dict:
    base = {"Title": "", "Artist": "", "Album": "", "Album Artist": ""}
    base.update(kwargs)
    return base


def test_seed_uses_shared_album_artist_and_album() -> None:
    tracks = [
        _track(Album="Discovery", **{"Album Artist": "Daft Punk"}, Title="One More Time"),
        _track(Album="Discovery", **{"Album Artist": "Daft Punk"}, Title="Aerodynamic"),
    ]
    seed = seed_query_from_tracks(tracks)
    assert seed.artist == "Daft Punk"
    assert seed.album == "Discovery"
    assert seed.text == "Daft Punk Discovery"


def test_seed_falls_back_to_artist_when_album_artist_missing() -> None:
    tracks = [
        _track(Album="Discovery", Artist="Daft Punk", Title="One More Time"),
        _track(Album="Discovery", Artist="Daft Punk", Title="Aerodynamic"),
    ]
    seed = seed_query_from_tracks(tracks)
    assert seed.artist == "Daft Punk"
    assert seed.album == "Discovery"


def test_seed_single_track_without_album_uses_artist_and_title() -> None:
    tracks = [_track(Artist="Aphex Twin", Title="Xtal")]
    seed = seed_query_from_tracks(tracks)
    assert seed.text == "Aphex Twin Xtal"
    assert seed.album == ""


def test_seed_single_track_with_album_but_no_artist_searches_album() -> None:
    tracks = [_track(Album="Discovery", Title="One More Time")]
    seed = seed_query_from_tracks(tracks)
    assert seed.text == "Discovery"
    assert seed.album == "Discovery"
    assert seed.artist == ""


def test_seed_shared_album_with_disagreeing_artists_searches_album() -> None:
    tracks = [
        _track(Album="Various Artists Compilation", Artist="Daft Punk", Title="One More Time"),
        _track(Album="Various Artists Compilation", Artist="Aphex Twin", Title="Xtal"),
    ]
    seed = seed_query_from_tracks(tracks)
    assert seed.text == "Various Artists Compilation"
    assert seed.album == "Various Artists Compilation"
    assert seed.artist == ""


def test_seed_mixed_selection_is_empty() -> None:
    tracks = [
        _track(Album="Discovery", **{"Album Artist": "Daft Punk"}),
        _track(Album="Homework", **{"Album Artist": "Daft Punk"}),
    ]
    seed = seed_query_from_tracks(tracks)
    assert seed.text == ""
    assert seed.album == ""


def test_seed_empty_selection_is_empty() -> None:
    assert seed_query_from_tracks([]).text == ""


def test_escape_lucene_escapes_quotes_and_backslashes() -> None:
    assert escape_lucene('say "hi"') == 'say \\"hi\\"'
    assert escape_lucene("back\\slash") == "back\\\\slash"


def test_fielded_query_when_artist_and_album_known() -> None:
    seed = SeedQuery(text="Daft Punk Discovery", artist="Daft Punk", album="Discovery")
    assert lucene_release_group_query(seed) == 'releasegroup:"Discovery" AND artist:"Daft Punk"'


def test_free_text_query_when_not_separable() -> None:
    seed = SeedQuery(text="daft punk discovery", artist="", album="")
    assert lucene_release_group_query(seed) == "daft punk discovery"


def test_fielded_query_escapes_quotes() -> None:
    seed = SeedQuery(text='x', artist='AC "DC"', album="Back")
    assert lucene_release_group_query(seed) == 'releasegroup:"Back" AND artist:"AC \\"DC\\""'
