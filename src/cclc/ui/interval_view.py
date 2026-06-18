"""View 4 - Interval: per-file label counts within a time window.

For one corpus and one dictionary tier, every annotation lying within a fixed
interval is collected; the table lists the count of each dictionary label per
file plus the dictionary coverage restricted to the interval.  The bounds are
adjustable by sliders and by exact millisecond spin boxes, kept in sync.
"""

from __future__ import annotations

import csv
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from cclc.core import analysis
from cclc.core.analysis import MissingTierError

from .controller import ProjectController


class IntervalView(QWidget):
    def __init__(self, controller: ProjectController) -> None:
        super().__init__()
        self.controller = controller
        self.controller.changed.connect(self._reload_corpora)
        self._updating = False
        self._extent = 0

        layout = QVBoxLayout(self)

        # --- selection row ---
        form = QHBoxLayout()
        self.corpus_combo = QComboBox()
        self.tier_combo = QComboBox()
        self.mode_combo = QComboBox()
        self.mode_combo.addItem("contained in interval", "contained")
        self.mode_combo.addItem("overlapping interval", "overlapping")
        self.case_check = QCheckBox("Case-sensitive")
        self.case_check.setChecked(True)
        form.addWidget(QLabel("Corpus:"))
        form.addWidget(self.corpus_combo)
        form.addWidget(QLabel("Tier:"))
        form.addWidget(self.tier_combo)
        form.addWidget(QLabel("Annotations:"))
        form.addWidget(self.mode_combo)
        form.addWidget(self.case_check)
        form.addStretch(1)
        layout.addLayout(form)

        # --- interval bounds: sliders + exact numbers ---
        bounds = QGridLayout()
        self.lo_spin = QSpinBox()
        self.hi_spin = QSpinBox()
        for spin in (self.lo_spin, self.hi_spin):
            spin.setSuffix(" ms")
            spin.setRange(0, 0)
        self.lo_slider = QSlider(Qt.Horizontal)
        self.hi_slider = QSlider(Qt.Horizontal)
        bounds.addWidget(QLabel("From:"), 0, 0)
        bounds.addWidget(self.lo_spin, 0, 1)
        bounds.addWidget(self.lo_slider, 0, 2)
        bounds.addWidget(QLabel("To:"), 1, 0)
        bounds.addWidget(self.hi_spin, 1, 1)
        bounds.addWidget(self.hi_slider, 1, 2)
        bounds.setColumnStretch(2, 1)
        layout.addLayout(bounds)

        self.status = QLabel("")
        self.status.setStyleSheet("color: #b00;")
        layout.addWidget(self.status)

        self.table = QTableWidget()
        layout.addWidget(self.table, 1)
        self.summary = QLabel("")
        layout.addWidget(self.summary)

        export_row = QHBoxLayout()
        export_row.addStretch(1)
        export_btn = QPushButton("Export CSV…")
        export_btn.clicked.connect(self._export_csv)
        export_row.addWidget(export_btn)
        layout.addLayout(export_row)

        # --- wiring ---
        self.corpus_combo.currentTextChanged.connect(self._on_corpus_changed)
        self.tier_combo.currentTextChanged.connect(self._recompute)
        self.mode_combo.currentIndexChanged.connect(self._recompute)
        self.case_check.toggled.connect(self._recompute)
        self.lo_spin.valueChanged.connect(self._lo_changed)
        self.lo_slider.valueChanged.connect(self._lo_changed)
        self.hi_spin.valueChanged.connect(self._hi_changed)
        self.hi_slider.valueChanged.connect(self._hi_changed)

        self._reload_corpora()

    # --- combo population ------------------------------------------------------

    def _current_corpus(self):
        return self.controller.project.get_corpus(self.corpus_combo.currentText())

    def _reload_corpora(self) -> None:
        current = self.corpus_combo.currentText()
        self.corpus_combo.blockSignals(True)
        self.corpus_combo.clear()
        self.corpus_combo.addItems(self.controller.project.corpus_names())
        idx = self.corpus_combo.findText(current)
        if idx >= 0:
            self.corpus_combo.setCurrentIndex(idx)
        self.corpus_combo.blockSignals(False)
        self._on_corpus_changed()

    def _on_corpus_changed(self) -> None:
        corpus = self._current_corpus()
        current_tier = self.tier_combo.currentText()
        self.tier_combo.blockSignals(True)
        self.tier_combo.clear()
        if corpus is not None and corpus.files:
            try:
                self.tier_combo.addItems(
                    analysis.dictionary_tiers(self.controller.project, corpus)
                )
            except Exception:  # noqa: BLE001
                pass
            idx = self.tier_combo.findText(current_tier)
            if idx >= 0:
                self.tier_combo.setCurrentIndex(idx)
        self.tier_combo.blockSignals(False)
        self._update_extent()
        self._recompute()

    def _update_extent(self) -> None:
        corpus = self._current_corpus()
        extent = (
            analysis.corpus_time_extent(self.controller.project, corpus)
            if corpus is not None
            else 0
        )
        if extent == self._extent:
            return
        old_extent = self._extent
        self._extent = extent
        self._updating = True
        for widget in (self.lo_spin, self.hi_spin, self.lo_slider, self.hi_slider):
            widget.setMaximum(extent)
        # Widen the window to the new full range when it previously spanned it.
        if self.hi_spin.value() == 0 or self.hi_spin.value() >= old_extent:
            self.hi_spin.setValue(extent)
            self.hi_slider.setValue(extent)
        self._updating = False

    # --- bound synchronisation ---------------------------------------------------

    def _lo_changed(self, value: int) -> None:
        if self._updating:
            return
        self._updating = True
        self.lo_spin.setValue(value)
        self.lo_slider.setValue(value)
        if value > self.hi_spin.value():  # keep From <= To by pushing the other bound
            self.hi_spin.setValue(value)
            self.hi_slider.setValue(value)
        self._updating = False
        self._recompute()

    def _hi_changed(self, value: int) -> None:
        if self._updating:
            return
        self._updating = True
        self.hi_spin.setValue(value)
        self.hi_slider.setValue(value)
        if value < self.lo_spin.value():
            self.lo_spin.setValue(value)
            self.lo_slider.setValue(value)
        self._updating = False
        self._recompute()

    # --- computation ---------------------------------------------------------

    def _recompute(self) -> None:
        if self._updating:
            return
        self.status.setText("")
        self.table.clear()
        self.table.setRowCount(0)
        self.table.setColumnCount(0)
        self.summary.setText("")

        corpus = self._current_corpus()
        tier = self.tier_combo.currentText()
        if corpus is None or not corpus.files or not tier:
            return

        try:
            result = analysis.interval_label_counts(
                self.controller.project,
                corpus,
                tier,
                self.lo_spin.value(),
                self.hi_spin.value(),
                mode=self.mode_combo.currentData(),
                case_sensitive=self.case_check.isChecked(),
            )
        except MissingTierError as exc:
            self.status.setText(str(exc))
            return
        except Exception as exc:  # noqa: BLE001
            self.status.setText(f"Could not analyse: {exc}")
            return

        labels = result.dictionary
        headers = ["File", *labels, "Out-of-dict", "Coverage"]
        self.table.setColumnCount(len(headers))
        self.table.setHorizontalHeaderLabels(headers)
        files = list(result.per_file_label_counts.keys())
        self.table.setRowCount(len(files))
        percents = result.per_file_percent
        covered = result.per_file_covered
        for row, path in enumerate(files):
            self.table.setItem(row, 0, QTableWidgetItem(path.name))
            counts = result.per_file_label_counts[path]
            for col, label in enumerate(labels, start=1):
                self.table.setItem(row, col, QTableWidgetItem(str(counts[label])))
            self.table.setItem(
                row, len(labels) + 1, QTableWidgetItem(str(result.out_of_dictionary[path]))
            )
            self.table.setItem(
                row,
                len(labels) + 2,
                QTableWidgetItem(
                    f"{percents[path]:.1f}%  ({covered[path]}/{result.dictionary_size})"
                ),
            )
        self.table.resizeColumnsToContents()
        self.summary.setText(
            f"Interval {result.start_ms}–{result.end_ms} ms   "
            f"mean coverage: {result.mean_coverage:.1f}%"
        )

    # --- export --------------------------------------------------------------

    def _export_csv(self) -> None:
        if self.table.rowCount() == 0:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Export CSV", "interval.csv", "CSV (*.csv)")
        if not path:
            return
        with Path(path).open("w", newline="", encoding="utf-8") as fh:
            writer = csv.writer(fh)
            writer.writerow(
                [
                    self.table.horizontalHeaderItem(c).text()
                    for c in range(self.table.columnCount())
                ]
            )
            for row in range(self.table.rowCount()):
                writer.writerow(
                    [
                        self.table.item(row, c).text() if self.table.item(row, c) else ""
                        for c in range(self.table.columnCount())
                    ]
                )
            writer.writerow([])
            writer.writerow([self.summary.text()])
