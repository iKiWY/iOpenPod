from __future__ import annotations

import pytest

pytest.importorskip("PyQt6.QtWidgets")


def _track(**kwargs: str) -> dict:
    base = {"Title": "One More Time", "Artist": "Daft Punk", "Album": "Discovery", "Album Artist": "Daft Punk"}
    base.update(kwargs)
    return base


def test_artwork_panel_exposes_a_search_signal() -> None:
    from iopenpod.gui.widgets.trackEditorDialog import _ArtworkPreviewPanel

    assert hasattr(_ArtworkPreviewPanel, "searchArtworkRequested")


def test_panel_has_a_find_artwork_online_button(qtbot) -> None:
    from iopenpod.gui.widgets.trackEditorDialog import _ArtworkPreviewPanel

    panel = _ArtworkPreviewPanel([], [_track()])
    qtbot.addWidget(panel)
    labels = {button.text() for button in panel.findChildren(type(panel._change_btn))}
    assert "Find Artwork Online" in labels


def test_search_button_emits_the_signal(qtbot) -> None:
    from iopenpod.gui.widgets.trackEditorDialog import _ArtworkPreviewPanel

    panel = _ArtworkPreviewPanel([], [_track()])
    qtbot.addWidget(panel)
    with qtbot.waitSignal(panel.searchArtworkRequested, timeout=1000):
        panel._search_btn.click()


def test_editor_seeds_the_search_from_the_selection() -> None:
    from iopenpod.artwork_search.query import seed_query_from_tracks

    seed = seed_query_from_tracks([_track(), _track(Title="Aerodynamic")])
    assert seed.artist == "Daft Punk"
    assert seed.album == "Discovery"
