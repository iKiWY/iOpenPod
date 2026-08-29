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
