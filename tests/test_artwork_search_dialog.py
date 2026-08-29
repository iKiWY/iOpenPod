from __future__ import annotations

import pytest

from iopenpod.artwork_search.models import ArtworkCandidate
from iopenpod.artwork_search.query import SeedQuery

pytest.importorskip("PyQt6.QtWidgets")


@pytest.fixture
def dialog(qtbot):
    from iopenpod.gui.widgets.artworkSearchDialog import ArtworkSearchDialog

    widget = ArtworkSearchDialog(
        SeedQuery(text="Daft Punk Discovery", artist="Daft Punk", album="Discovery"),
        auto_search=False,
        load_thumbnails=False,
    )
    qtbot.addWidget(widget)
    return widget


def _candidate(url: str, source: str = "iTunes") -> ArtworkCandidate:
    return ArtworkCandidate(
        title="Discovery",
        artist="Daft Punk",
        source=source,
        thumb_url=url,
        full_url=url,
        year="2001",
        width=1200,
    )


def test_search_box_is_pre_seeded(dialog) -> None:
    assert dialog.search_text() == "Daft Punk Discovery"


def test_no_image_chosen_before_acceptance(dialog) -> None:
    assert dialog.chosen_image_path() is None


def test_results_render_cards(dialog) -> None:
    dialog.append_results([_candidate("https://a/1.jpg"), _candidate("https://a/2.jpg")])
    assert dialog.result_count() == 2


def test_duplicate_full_urls_are_suppressed(dialog) -> None:
    dialog.append_results([_candidate("https://a/1.jpg")])
    dialog.append_results([_candidate("https://a/1.jpg", source="Cover Art Archive")])
    assert dialog.result_count() == 1


def test_different_urls_from_both_providers_both_kept(dialog) -> None:
    dialog.append_results([_candidate("https://a/1.jpg")])
    dialog.append_results([_candidate("https://b/1.jpg", source="Cover Art Archive")])
    assert dialog.result_count() == 2


def test_results_are_capped(dialog) -> None:
    from iopenpod.gui.widgets.artworkSearchDialog import MAX_RESULTS

    dialog.append_results([_candidate(f"https://a/{index}.jpg") for index in range(MAX_RESULTS + 10)])
    assert dialog.result_count() == MAX_RESULTS


def test_one_provider_failing_keeps_the_other_results(dialog) -> None:
    dialog.append_results([_candidate("https://a/1.jpg")])
    dialog.note_provider_failed("Cover Art Archive")
    assert dialog.result_count() == 1
    assert "Cover Art Archive" in dialog.status_text()


def test_both_providers_failing_shows_error_state(dialog) -> None:
    dialog.note_provider_failed("iTunes")
    dialog.note_provider_failed("Cover Art Archive")
    assert dialog.result_count() == 0
    assert dialog.is_showing_error()


def test_stale_generation_results_are_ignored(dialog) -> None:
    # Simulate a search in flight (generation 1) that is then superseded by
    # a new search (generation 2) before the old workers report back.
    dialog._search_generation = 1
    stale_generation = dialog._search_generation
    dialog._search_generation += 1
    dialog._pending_providers = 2

    dialog.append_results([_candidate("https://old/1.jpg")], _generation=stale_generation)
    assert dialog.result_count() == 0
    assert dialog._pending_providers == 2  # the new search's counter must be untouched

    dialog.append_results([_candidate("https://new/1.jpg")], _generation=dialog._search_generation)
    assert dialog.result_count() == 1


def test_stale_generation_failure_does_not_decrement_pending_providers(dialog) -> None:
    dialog._search_generation = 1
    stale_generation = dialog._search_generation
    dialog._search_generation += 1
    dialog._pending_providers = 2

    dialog.note_provider_failed("iTunes", _generation=stale_generation)
    assert dialog._pending_providers == 2
    assert dialog._failed_providers == []
    assert not dialog.is_showing_error()
