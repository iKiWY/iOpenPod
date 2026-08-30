from __future__ import annotations

import pytest
from PyQt6.QtWidgets import QApplication

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


def test_fast_provider_cannot_starve_slow_provider(dialog) -> None:
    """A full-budget iTunes batch must still leave room for CAA results.

    Regression test for the whole-branch finding where an unbounded iTunes
    worker could return a full page of MAX_RESULTS candidates before Cover
    Art Archive ever landed, so the per-source cap in ``append_results``
    zeroed out every later CAA result via the old global-only cap.
    """
    from iopenpod.gui.widgets.artworkSearchDialog import MAX_RESULTS

    dialog._pending_providers = 2  # both providers still outstanding
    dialog.append_results([_candidate(f"https://itunes/{index}.jpg") for index in range(MAX_RESULTS)])
    dialog.append_results(
        [_candidate(f"https://caa/{index}.jpg", source="Cover Art Archive") for index in range(6)]
    )

    sources = {candidate.source for candidate in dialog._candidates}
    assert "Cover Art Archive" in sources
    itunes_count = sum(1 for candidate in dialog._candidates if candidate.source == "iTunes")
    caa_count = sum(1 for candidate in dialog._candidates if candidate.source == "Cover Art Archive")
    assert itunes_count == MAX_RESULTS // 2
    assert caa_count == 6


def test_last_remaining_provider_may_use_the_remainder(dialog) -> None:
    """Once the other provider is done, the last one isn't capped to its fixed share."""
    from iopenpod.gui.widgets.artworkSearchDialog import MAX_RESULTS

    dialog._pending_providers = 2
    dialog.append_results([_candidate("https://itunes/1.jpg")])  # only 1 of its share used
    dialog._on_worker_finished()  # iTunes's worker settles; only CAA is left outstanding

    dialog.append_results(
        [_candidate(f"https://caa/{index}.jpg", source="Cover Art Archive") for index in range(MAX_RESULTS)]
    )
    caa_count = sum(1 for candidate in dialog._candidates if candidate.source == "Cover Art Archive")
    assert caa_count == MAX_RESULTS - 1


def test_a_worker_that_only_emits_finished_still_settles_the_dialog(dialog) -> None:
    """A cancelled Worker (DeviceManager.cancel_all_operations mid-search) emits only ``finished``.

    Regression test: with neither result nor error connected to decrement
    ``_pending_providers``, the dialog used to stay stuck on "Searching…"
    forever whenever a device disconnect cancelled one of the two workers.
    """
    dialog._pending_providers = 2
    dialog._search_btn.setEnabled(False)
    dialog._search_input.setEnabled(False)

    dialog.append_results([_candidate("https://a/1.jpg")])  # iTunes lands normally
    dialog._on_worker_finished()  # iTunes's own `finished`
    assert not dialog._search_btn.isEnabled()  # still waiting on the cancelled provider

    dialog._on_worker_finished()  # the cancelled Cover Art Archive worker: only `finished` fires

    assert dialog._search_btn.isEnabled()
    assert dialog._search_input.isEnabled()
    assert dialog.result_count() == 1


def test_both_providers_failing_surfaces_the_real_error(dialog) -> None:
    """The both-failed panel must show the actual provider error, not always the generic copy."""
    from iopenpod.artwork_search.errors import ArtworkErrorInfo, ArtworkSearchError

    busy = ArtworkSearchError(ArtworkErrorInfo(title="MusicBrainz is busy", message="Try again in a moment.", code="MB-BUSY"))
    dialog.note_provider_failed("Cover Art Archive", (ArtworkSearchError, busy, ""))
    dialog.note_provider_failed("iTunes", (RuntimeError, RuntimeError("boom"), ""))

    assert dialog.is_showing_error()
    # The first provider to fail (Cover Art Archive here) sets the reason
    # shown in the both-failed panel, not the old hardcoded generic copy.
    assert dialog._first_failure_info is busy.info


def test_download_abandoned_by_closing_the_dialog_deletes_the_temp_file(dialog, tmp_path) -> None:
    """Closing the dialog mid-download must not leak the downloaded temp file."""
    leaked = tmp_path / "leaked.png"
    leaked.write_bytes(b"fake-png")

    dialog.reject()  # simulates Cancel/Esc/window-close while a download is in flight
    assert dialog._abandoned is True

    dialog._on_downloaded(str(leaked))

    assert not leaked.exists()
    assert dialog.chosen_image_path() is None


def _cards(dialog) -> list:
    """Every result card currently laid out in the grid, in grid order."""
    grid = dialog._grid
    return [
        grid.itemAt(index).widget()
        for index in range(grid.count())
        if grid.itemAt(index).widget() is not None
    ]


def _shown(dialog, width: int, height: int = 700):
    """Show the dialog at a real size so the viewport has a usable width.

    Column count is derived from the viewport, so a dialog that was never
    shown reports a meaningless single column.
    """
    dialog.resize(width, height)
    dialog.show()
    dialog._grid.activate()
    return dialog


def test_every_card_is_the_same_fixed_size(dialog) -> None:
    """Cards must not resize to fill the row, whatever their text length."""
    from iopenpod.gui.widgets.artworkSearchDialog import CARD_WIDTH

    _shown(dialog, 900)
    dialog.append_results([
        _candidate("https://a/1.jpg"),
        ArtworkCandidate(
            title="A Very Long Album Title That Would Otherwise Wrap Onto Several Lines",
            artist="An Extremely Long Artist Name That Would Also Wrap",
            source="iTunes",
            thumb_url="https://a/2.jpg",
            full_url="https://a/2.jpg",
            year="1999",
            width=1200,
        ),
    ])
    dialog._grid.activate()

    sizes = {(card.width(), card.height()) for card in _cards(dialog)}
    assert len(sizes) == 1, f"cards differ in size: {sizes}"
    assert sizes.pop()[0] == CARD_WIDTH


def test_a_lone_result_does_not_stretch_to_the_full_width(dialog) -> None:
    """One result used to expand across the whole viewport."""
    from iopenpod.gui.widgets.artworkSearchDialog import CARD_WIDTH

    _shown(dialog, 900)
    dialog.append_results([_candidate("https://a/1.jpg")])
    dialog._grid.activate()

    assert _cards(dialog)[0].width() == CARD_WIDTH


def test_late_results_do_not_move_the_cards_already_on_screen(dialog) -> None:
    """The misclick bug: a slow provider landing must not shift existing cards.

    Cover Art Archive answers well after iTunes, so a user reaching for an
    iTunes result must not have it move out from under the cursor.
    """
    _shown(dialog, 900)
    dialog.append_results([_candidate(f"https://a/{i}.jpg") for i in range(2)])
    dialog._grid.activate()
    before = [(card.pos(), card.size()) for card in _cards(dialog)]

    dialog.append_results([
        _candidate(f"https://b/{i}.jpg", source="Cover Art Archive") for i in range(6)
    ])
    dialog._grid.activate()
    after = [(card.pos(), card.size()) for card in _cards(dialog)][: len(before)]

    assert after == before


def test_column_count_follows_the_window_width(dialog) -> None:
    """Wider window, more cards per row — not a hardcoded three."""
    from iopenpod.gui.widgets.artworkSearchDialog import columns_for_width

    _shown(dialog, 640)
    dialog.append_results([_candidate(f"https://a/{i}.jpg") for i in range(12)])

    seen = {}
    for width in (640, 900, 1200, 900, 640):
        dialog.resize(width, 700)
        dialog._grid.activate()
        seen[width] = dialog.grid_columns()

    assert seen[900] > seen[640], f"widening did not add a column: {seen}"
    assert seen[1200] > seen[900], f"widening did not add a column: {seen}"
    # Shrinking must give the columns back, not leave them stuck wide.
    assert seen[640] == columns_for_width(dialog._viewport_width())


def test_widening_then_narrowing_does_not_overlap_rows(dialog) -> None:
    """Going back to a narrower window must give the extra row real space.

    The grid caches its size hint. Widening drops a row and shrinks the
    host; narrowing again needs that row back, and without invalidating the
    hint the host stays short and the fixed-height cards are stacked on top
    of one another — cards overlapping by ~70px in the real dialog.
    """
    _shown(dialog, 720)
    dialog.append_results([_candidate(f"https://a/{i}.jpg") for i in range(12)])

    for width in (720, 1100, 720, 900, 640):
        dialog.resize(width, 640)
        QApplication.processEvents()
        dialog._grid.activate()
        QApplication.processEvents()
        cards = _cards(dialog)
        card_height = max(card.height() for card in cards)
        rows = sorted({card.y() for card in cards})
        pitches = [b - a for a, b in zip(rows, rows[1:], strict=False)]
        assert all(pitch >= card_height for pitch in pitches), (
            f"rows overlap at width {width}: pitches={pitches} card_height={card_height}"
        )
