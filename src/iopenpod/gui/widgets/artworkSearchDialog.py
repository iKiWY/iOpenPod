"""Online artwork search dialog — iTunes and Cover Art Archive.

Searches run on background workers to keep the UI responsive.  The dialog's
only output is a local temp image path, which the track editor feeds into the
same crop dialog the file picker uses.
"""

from __future__ import annotations

import logging

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFont, QImage, QPixmap
from PyQt6.QtWidgets import (
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from iopenpod.artwork_search.models import ArtworkCandidate
from iopenpod.artwork_search.query import SeedQuery

from ..hidpi import scale_pixmap_for_display
from ..styles import (
    FONT_FAMILY,
    LABEL_SECONDARY,
    Metrics,
    accent_btn_css,
    btn_css,
    input_css,
    make_label,
    make_scroll_area,
    paint_css,
)
from .podcastStates import PodcastStatePanel

log = logging.getLogger(__name__)

MAX_RESULTS = 24
GRID_COLUMNS = 3
THUMB_SIZE = 140


def _provider_modules():
    """The artwork provider modules, each declaring its own SOURCE_NAME.

    Imported lazily (matching the local-import style used elsewhere in this
    dialog and in podcastSearchDialog.py) so importing this module never
    pulls in the network-facing provider modules at load time.
    """
    from iopenpod.artwork_search import coverart, itunes

    return (itunes, coverart)


class ArtworkSearchDialog(QDialog):
    """Search two keyless providers for album art, or paste an image URL."""

    # Carries the downloaded temp image path once a pick (or pasted URL)
    # finishes downloading. The dialog stays open when this fires — the
    # track editor runs the crop dialog on top of it and only closes this
    # dialog itself once the crop is accepted, so cancelling the cropper
    # returns to a still-open search dialog with results intact.
    imageReady = pyqtSignal(str)

    def __init__(
        self,
        seed: SeedQuery,
        parent: QWidget | None = None,
        *,
        auto_search: bool = True,
        load_thumbnails: bool = True,
    ):
        super().__init__(parent)
        self._seed = seed
        self._load_thumbnails = load_thumbnails
        self._chosen_path: str | None = None
        self._candidates: list[ArtworkCandidate] = []
        self._seen_urls: set[str] = set()
        self._source_counts: dict[str, int] = {}
        self._failed_providers: list[str] = []
        self._first_failure_info = None
        self._pending_providers = 0
        self._showing_error = False
        # Set once this dialog itself is rejected or closed (Cancel, Esc, the
        # window-manager close control). A download already in flight when
        # that happens still lands afterwards; _on_downloaded checks this to
        # delete the temp file instead of emitting imageReady into a dialog
        # nobody is looking at anymore, which would otherwise leak the file.
        self._abandoned = False
        # Bumped on every _on_search call so late-arriving results/failures
        # from a superseded search (the user re-searched before the old
        # workers finished) can be told apart from the current one and
        # ignored, instead of corrupting the new search's candidate list
        # and pending-provider count.
        self._search_generation = 0

        self.setWindowTitle("Find Artwork Online")
        self.setMinimumSize(640, 560)
        self.resize(720, 640)
        self.setStyleSheet(f"""
            QDialog {{
                background: {paint_css('modal.background')};
            }}
        """)

        self._build_ui()
        if auto_search and seed.text.strip():
            self._on_search()

    # ── Public surface used by the track editor and by tests ─────────────

    def chosen_image_path(self) -> str | None:
        """Temp PNG path for the picked image; only valid once accepted."""
        return self._chosen_path

    def search_text(self) -> str:
        return self._search_input.text()

    def result_count(self) -> int:
        return len(self._candidates)

    def status_text(self) -> str:
        return self._status_label.text()

    def is_showing_error(self) -> bool:
        return self._showing_error

    # ── UI construction ──────────────────────────────────────────────────

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        search_row = QHBoxLayout()
        search_row.setSpacing(8)

        self._search_input = QLineEdit()
        self._search_input.setText(self._seed.text)
        self._search_input.setPlaceholderText("Search for an album…")
        self._search_input.setFont(QFont(FONT_FAMILY, Metrics.FONT_MD))
        self._search_input.setStyleSheet(input_css(padding="8px 12px"))
        self._search_input.returnPressed.connect(self._on_search)
        search_row.addWidget(self._search_input, stretch=1)

        self._search_btn = QPushButton("Search")
        self._search_btn.setFont(QFont(FONT_FAMILY, Metrics.FONT_MD))
        self._search_btn.setStyleSheet(accent_btn_css())
        self._search_btn.setFixedHeight(36)
        self._search_btn.clicked.connect(self._on_search)
        search_row.addWidget(self._search_btn)

        layout.addLayout(search_row)

        self._status_label = make_label(
            "Search for album artwork",
            size=Metrics.FONT_SM,
            style=LABEL_SECONDARY(),
        )
        layout.addWidget(self._status_label)

        self._results_container = QWidget()
        self._outer_layout = QVBoxLayout(self._results_container)
        self._outer_layout.setContentsMargins(0, 0, 0, 0)
        self._outer_layout.setSpacing(8)

        self._state_panel = PodcastStatePanel(compact=True)
        self._state_panel.show_empty(
            "Find album artwork",
            "Search by artist and album, or paste an image address below.",
            glyph="album",
        )
        self._state_panel.action_clicked.connect(self._on_search)
        self._outer_layout.addWidget(self._state_panel)

        self._grid_host = QWidget()
        self._grid = QGridLayout(self._grid_host)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setSpacing(10)
        self._outer_layout.addWidget(self._grid_host)
        self._outer_layout.addStretch()

        scroll = make_scroll_area(extra_css=f"""
            QScrollArea {{
                border: 1px solid {paint_css('border.subtle')};
                border-radius: {Metrics.BORDER_RADIUS_SM}px;
            }}
        """)
        scroll.setWidget(self._results_container)
        layout.addWidget(scroll, stretch=1)

        url_row = QHBoxLayout()
        url_row.setSpacing(8)
        url_row.addWidget(
            make_label("Or paste image URL:", size=Metrics.FONT_SM, style=LABEL_SECONDARY())
        )

        self._url_input = QLineEdit()
        self._url_input.setPlaceholderText("https://example.com/cover.jpg")
        self._url_input.setFont(QFont(FONT_FAMILY, Metrics.FONT_SM))
        self._url_input.setStyleSheet(input_css(padding="6px 10px"))
        self._url_input.returnPressed.connect(self._on_use_url)
        url_row.addWidget(self._url_input, stretch=1)

        self._url_btn = QPushButton("Use URL")
        self._url_btn.setFont(QFont(FONT_FAMILY, Metrics.FONT_SM))
        self._url_btn.setStyleSheet(accent_btn_css())
        self._url_btn.setFixedHeight(32)
        self._url_btn.clicked.connect(self._on_use_url)
        url_row.addWidget(self._url_btn)

        layout.addLayout(url_row)

        close_btn = QPushButton("Cancel")
        close_btn.setFont(QFont(FONT_FAMILY, Metrics.FONT_MD))
        close_btn.setStyleSheet(btn_css())
        close_btn.setFixedHeight(36)
        close_btn.clicked.connect(self.reject)
        layout.addWidget(close_btn, alignment=Qt.AlignmentFlag.AlignRight)

    # ── Search ───────────────────────────────────────────────────────────

    def _on_search(self) -> None:
        query = self._search_input.text().strip()
        if not query:
            return

        from iopenpod.application.runtime import ThreadPoolSingleton, Worker

        modules = _provider_modules()

        seed = SeedQuery(text=query, artist=self._seed.artist, album=self._seed.album)
        if query != self._seed.text:
            # The user retyped the box, so the separated parts no longer apply.
            seed = SeedQuery(text=query)

        self._clear_results()
        self._failed_providers = []
        self._first_failure_info = None
        self._showing_error = False
        self._pending_providers = len(modules)
        self._search_generation += 1
        generation = self._search_generation
        self._search_btn.setEnabled(False)
        self._search_input.setEnabled(False)
        self._status_label.setText("Searching…")
        self._grid_host.hide()
        self._state_panel.show()
        provider_names = ", ".join(module.SOURCE_NAME for module in modules)
        self._state_panel.show_loading("Searching for artwork…", f"Checking {provider_names}.")

        # Each provider gets a guaranteed share of the grid so a fast
        # provider (iTunes, ~300ms) cannot answer with a full page before a
        # slow one (MusicBrainz, rate-limited to 1req/s plus a second CAA
        # round trip) ever gets a chance to contribute.
        per_provider_budget = max(1, MAX_RESULTS // len(modules))

        pool = ThreadPoolSingleton.get_instance()
        for module in modules:
            name = module.SOURCE_NAME
            worker = Worker(module.search, seed, per_provider_budget)
            worker.signals.result.connect(
                lambda candidates, gen=generation: self.append_results(candidates, _generation=gen)
            )
            worker.signals.error.connect(
                lambda error_tuple, provider=name, gen=generation: self.note_provider_failed(
                    provider, error_tuple, _generation=gen
                )
            )
            worker.signals.finished.connect(lambda gen=generation: self._on_worker_finished(_generation=gen))
            pool.start(worker)

    def append_results(self, candidates: list[ArtworkCandidate], *, _generation: int | None = None) -> None:
        """Add one provider's results to the grid. Safe to call repeatedly.

        ``_generation`` lets a background worker's callback identify which
        search it belongs to; a result from a search that has since been
        superseded by a newer one is dropped rather than mixed into the
        current results. Direct callers (tests, and the picking code below)
        omit it, which always applies unconditionally.

        Each source is capped at its guaranteed share (``MAX_RESULTS //``
        provider count) so long as another provider is still outstanding;
        once every other provider has already reported in (success or
        failure — see ``_on_worker_finished``/``note_provider_failed``),
        the last one may use whatever grid capacity remains rather than
        being held to its fixed share.
        """
        if _generation is not None and _generation != self._search_generation:
            log.debug("Dropping stale artwork results from generation %s (current %s)", _generation, self._search_generation)
            return

        modules_count = max(1, len(_provider_modules()))
        base_budget = max(1, MAX_RESULTS // modules_count)
        effective_budget = MAX_RESULTS if self._pending_providers <= 1 else base_budget

        added = False
        for candidate in candidates or []:
            if len(self._candidates) >= MAX_RESULTS:
                break
            if candidate.full_url in self._seen_urls:
                continue
            if self._source_counts.get(candidate.source, 0) >= effective_budget:
                continue
            self._seen_urls.add(candidate.full_url)
            self._candidates.append(candidate)
            self._source_counts[candidate.source] = self._source_counts.get(candidate.source, 0) + 1
            self._add_card(candidate, len(self._candidates) - 1)
            added = True

        if added:
            self._showing_error = False
            self._state_panel.hide()
            self._grid_host.show()
        self._refresh_status()

    def note_provider_failed(self, provider: str, error_tuple=None, *, _generation: int | None = None) -> None:
        """Record a provider failure. Only both failing is fatal.

        ``_generation`` mirrors ``append_results``: a failure reported by a
        superseded search's worker is dropped instead of being counted
        against the current search's pending-provider total.

        ``error_tuple`` is the ``(exc_type, value, traceback)`` tuple from
        ``Worker.signals.error``; its ``describe_artwork_error`` info is kept
        so the both-failed panel can show the real reason (MusicBrainz busy,
        an HTTP status, etc.) instead of always the same generic copy.
        """
        if _generation is not None and _generation != self._search_generation:
            log.debug("Dropping stale provider failure from generation %s (current %s)", _generation, self._search_generation)
            return

        if provider not in self._failed_providers:
            self._failed_providers.append(provider)

        if error_tuple is not None and self._first_failure_info is None:
            from iopenpod.artwork_search.errors import describe_artwork_error

            self._first_failure_info = describe_artwork_error(error_tuple[1])

        provider_count = len(_provider_modules())
        if not self._candidates and len(self._failed_providers) >= provider_count:
            self._showing_error = True
            self._grid_host.hide()
            self._state_panel.show()
            info = self._first_failure_info
            if info is not None:
                self._state_panel.show_error(info.title, info.message, code=info.code)
            else:
                self._state_panel.show_error(
                    "Artwork search did not work",
                    "Neither artwork service answered. Check your connection and try again.",
                )
        self._refresh_status()

    def _on_worker_finished(self, *, _generation: int | None = None) -> None:
        """Decrement the pending-provider count. Connected to every worker's ``finished``.

        ``Worker.run`` (application/runtime.py) emits exactly one
        ``finished`` per run in all three outcomes: result, error, or being
        cancelled with neither (which happens when
        ``DeviceManager.cancel_all_operations()`` fires mid-search, e.g. on
        eject or a device disconnect that is not user-driven). Decrementing
        here — instead of in ``append_results``/``note_provider_failed``,
        which only run on two of those three outcomes — is what keeps a
        cancelled search from wedging the dialog on "Searching…" forever.
        """
        if _generation is not None and _generation != self._search_generation:
            log.debug("Dropping stale worker-finished from generation %s (current %s)", _generation, self._search_generation)
            return
        self._pending_providers = max(0, self._pending_providers - 1)
        self._refresh_status()

    def _refresh_status(self) -> None:
        if self._pending_providers > 0 and not self._candidates:
            return

        if self._pending_providers == 0:
            self._search_btn.setEnabled(True)
            self._search_input.setEnabled(True)

        count = len(self._candidates)
        if count:
            text = f"Found {count} cover{'s' if count != 1 else ''}"
        elif self._showing_error:
            text = "Artwork search did not work"
        elif self._pending_providers == 0:
            text = "No artwork found"
            self._grid_host.hide()
            self._state_panel.show()
            self._state_panel.show_empty(
                "No artwork found",
                "Try a different album name, or paste an image address below.",
                glyph="album",
            )
        else:
            text = "Searching…"

        if self._failed_providers:
            text += f"  ·  {', '.join(self._failed_providers)} did not respond"
        self._status_label.setText(text)

    def _clear_results(self) -> None:
        self._candidates = []
        self._seen_urls = set()
        self._source_counts = {}
        while self._grid.count():
            item = self._grid.takeAt(0)
            widget = item.widget() if item is not None else None
            if widget is not None:
                widget.deleteLater()

    def _add_card(self, candidate: ArtworkCandidate, index: int) -> None:
        card = _ArtworkResultCard(candidate, self, load_thumbnail=self._load_thumbnails)
        card.picked.connect(self._on_pick)
        self._grid.addWidget(card, index // GRID_COLUMNS, index % GRID_COLUMNS)

    # ── Picking ──────────────────────────────────────────────────────────

    def _on_pick(self, url: str) -> None:
        self._download_and_accept(url, action="download artwork")

    def _on_use_url(self) -> None:
        url = self._url_input.text().strip()
        if url:
            self._download_and_accept(url, action="download that image")

    def _download_and_accept(self, url: str, *, action: str) -> None:
        from iopenpod.application.runtime import ThreadPoolSingleton, Worker
        from iopenpod.artwork_search.download import fetch_image, save_temp_image

        def _job() -> str:
            return save_temp_image(fetch_image(url))

        self.setEnabled(False)
        self._status_label.setText("Downloading…")

        worker = Worker(_job)
        worker.signals.result.connect(self._on_downloaded)
        worker.signals.error.connect(
            lambda error_tuple, act=action: self._on_download_error(error_tuple, act)
        )
        ThreadPoolSingleton.get_instance().start(worker)

    def _on_downloaded(self, path: str) -> None:
        if self._abandoned:
            # The dialog was closed (window-manager close, Cancel, Esc)
            # while this download was still in flight. A top-level QDialog
            # can be closed regardless of setEnabled(False), so this worker
            # kept running and only now delivered its result to a dialog
            # nobody is looking at. Nothing downstream will ever claim this
            # temp file, so delete it here instead of leaking it.
            self._delete_temp_file(path)
            return

        self.setEnabled(True)
        self._chosen_path = path
        self._status_label.setText("Ready")
        self.imageReady.emit(path)

    def _on_download_error(self, error_tuple, action: str) -> None:
        if self._abandoned:
            return

        from PyQt6.QtWidgets import QMessageBox

        from iopenpod.artwork_search.errors import describe_artwork_error

        self.setEnabled(True)
        _type, value, _tb = error_tuple
        info = describe_artwork_error(value, action=action)
        self._status_label.setText(info.title)
        QMessageBox.warning(self, info.title, info.message)

    @staticmethod
    def _delete_temp_file(path: str) -> None:
        import os

        try:
            os.remove(path)
        except OSError:
            pass

    def reject(self) -> None:
        self._abandoned = True
        super().reject()

    def closeEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        self._abandoned = True
        super().closeEvent(event)


class _ArtworkResultCard(QFrame):
    """One clickable cover result with an async thumbnail."""

    picked = pyqtSignal(str)

    def __init__(self, candidate: ArtworkCandidate, parent: QWidget | None = None, *, load_thumbnail: bool = True):
        super().__init__(parent)
        self._candidate = candidate
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setStyleSheet(f"""
            _ArtworkResultCard {{
                background: {paint_css('surface.inset')};
                border: 1px solid {paint_css('border.subtle')};
                border-radius: {Metrics.BORDER_RADIUS_SM}px;
            }}
            _ArtworkResultCard:hover {{
                background: {paint_css('surface.hover')};
            }}
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(4)

        self._art_label = QLabel()
        self._art_label.setFixedSize(THUMB_SIZE, THUMB_SIZE)
        self._art_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._art_label.setStyleSheet(f"""
            background: {paint_css('surface.raised')};
            border-radius: {Metrics.BORDER_RADIUS_SM}px;
            color: {paint_css('text.tertiary')};
        """)
        layout.addWidget(self._art_label, alignment=Qt.AlignmentFlag.AlignHCenter)

        title = make_label(candidate.title, size=Metrics.FONT_SM, weight=QFont.Weight.DemiBold)
        title.setWordWrap(True)
        title.setFixedWidth(THUMB_SIZE)
        layout.addWidget(title)

        artist = make_label(candidate.artist, size=Metrics.FONT_SM, style=LABEL_SECONDARY())
        artist.setWordWrap(True)
        artist.setFixedWidth(THUMB_SIZE)
        layout.addWidget(artist)

        detail = make_label(candidate.detail_line, size=Metrics.FONT_SM, style=LABEL_SECONDARY())
        detail.setFixedWidth(THUMB_SIZE)
        layout.addWidget(detail)

        if load_thumbnail:
            self._load_thumbnail(candidate.thumb_url)

    def mousePressEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        if event.button() == Qt.MouseButton.LeftButton:
            self.picked.emit(self._candidate.full_url)
        super().mousePressEvent(event)

    def _load_thumbnail(self, url: str) -> None:
        if not url:
            return

        from iopenpod.application.runtime import ThreadPoolSingleton, Worker
        from iopenpod.artwork_search.download import fetch_image

        worker = Worker(fetch_image, url)
        worker.signals.result.connect(self._on_thumbnail)
        ThreadPoolSingleton.get_instance().start(worker)

    def _on_thumbnail(self, data: bytes) -> None:
        image = QImage()
        if not image.loadFromData(data):
            return
        self._art_label.setPixmap(
            scale_pixmap_for_display(
                QPixmap.fromImage(image),
                THUMB_SIZE,
                THUMB_SIZE,
                widget=self._art_label,
                aspect_mode=Qt.AspectRatioMode.KeepAspectRatio,
                transform_mode=Qt.TransformationMode.SmoothTransformation,
            )
        )
