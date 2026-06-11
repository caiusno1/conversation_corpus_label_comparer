"""View 2 - Analysis: annotation counts and dictionary coverage for a corpus."""

from __future__ import annotations

import csv
from pathlib import Path

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from cclc.core import analysis
from cclc.core.analysis import MissingTierError

from .controller import ProjectController


class AnalysisView(QWidget):
    def __init__(self, controller: ProjectController) -> None:
        super().__init__()
        self.controller = controller
        self.controller.changed.connect(self._reload_corpora)

        layout = QVBoxLayout(self)

        form = QFormLayout()
        self.corpus_combo = QComboBox()
        self.tier_combo = QComboBox()
        self.label_combo = QComboBox()
        self.case_check = QCheckBox("Case-sensitive matching")
        self.case_check.setChecked(True)
        form.addRow("Corpus:", self.corpus_combo)
        form.addRow("Tier:", self.tier_combo)
        form.addRow("Label:", self.label_combo)
        form.addRow("", self.case_check)
        layout.addLayout(form)

        self.status = QLabel("")
        self.status.setStyleSheet("color: #b00;")
        layout.addWidget(self.status)

        layout.addWidget(QLabel("Counts"))
        self.counts_table = QTableWidget()
        layout.addWidget(self.counts_table)
        self.counts_summary = QLabel("")
        layout.addWidget(self.counts_summary)

        layout.addWidget(QLabel("Dictionary coverage"))
        self.coverage_table = QTableWidget()
        layout.addWidget(self.coverage_table)
        self.coverage_summary = QLabel("")
        layout.addWidget(self.coverage_summary)

        buttons = QHBoxLayout()
        export = QPushButton("Export CSV…")
        export.clicked.connect(self._export_csv)
        buttons.addStretch(1)
        buttons.addWidget(export)
        layout.addLayout(buttons)

        self.corpus_combo.currentTextChanged.connect(self._reload_tiers)
        self.tier_combo.currentTextChanged.connect(self._reload_labels)
        self.label_combo.currentTextChanged.connect(self._recompute)
        self.case_check.toggled.connect(self._recompute)

        self._reload_corpora()

    # --- combo population ----------------------------------------------------

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
        self._reload_tiers()

    def _reload_tiers(self) -> None:
        corpus = self._current_corpus()
        self.tier_combo.blockSignals(True)
        self.tier_combo.clear()
        if corpus is not None and corpus.files:
            try:
                tiers = analysis.dictionary_tiers(self.controller.project, corpus)
                self.tier_combo.addItems(tiers)
            except Exception:  # noqa: BLE001 - unreadable file etc.
                pass
        self.tier_combo.blockSignals(False)
        self._reload_labels()

    def _reload_labels(self) -> None:
        corpus = self._current_corpus()
        tier = self.tier_combo.currentText()
        self.label_combo.blockSignals(True)
        self.label_combo.clear()
        if corpus is not None and tier:
            try:
                labels = analysis.union_dictionary(self.controller.project, corpus, tier)
                self.label_combo.addItems(labels)
            except Exception:  # noqa: BLE001
                pass
        self.label_combo.blockSignals(False)
        self._recompute()

    # --- computation ---------------------------------------------------------

    def _recompute(self) -> None:
        self.status.setText("")
        self.counts_table.clear()
        self.coverage_table.clear()
        self.counts_summary.setText("")
        self.coverage_summary.setText("")

        corpus = self._current_corpus()
        tier = self.tier_combo.currentText()
        label = self.label_combo.currentText()
        if corpus is None or not corpus.files or not tier:
            return
        case = self.case_check.isChecked()

        try:
            cov = analysis.coverage(self.controller.project, corpus, tier, case)
            counts = (
                analysis.count_label(self.controller.project, corpus, tier, label, case)
                if label
                else None
            )
        except MissingTierError as exc:
            self.status.setText(str(exc))
            return
        except Exception as exc:  # noqa: BLE001
            self.status.setText(f"Could not analyse: {exc}")
            return

        if counts is not None:
            self._fill_counts(counts)
        self._fill_coverage(cov)

    def _fill_counts(self, counts) -> None:
        files = list(counts.per_file.keys())
        self.counts_table.setColumnCount(3)
        self.counts_table.setHorizontalHeaderLabels(["File", "Count", "Out-of-dictionary"])
        self.counts_table.setRowCount(len(files))
        for row, path in enumerate(files):
            self.counts_table.setItem(row, 0, QTableWidgetItem(path.name))
            self.counts_table.setItem(row, 1, QTableWidgetItem(str(counts.per_file[path])))
            self.counts_table.setItem(
                row, 2, QTableWidgetItem(str(counts.out_of_dictionary.get(path, 0)))
            )
        self.counts_table.resizeColumnsToContents()
        stdev = counts.stdev
        stdev_text = f"{stdev:.2f}" if stdev is not None else "n/a"
        self.counts_summary.setText(
            f"Label “{counts.label}”  -  total: {counts.total}   "
            f"mean/file: {counts.mean:.2f}   σ (sample): {stdev_text}"
        )

    def _fill_coverage(self, cov) -> None:
        percents = cov.per_file_percent
        files = list(cov.per_file_covered.keys())
        self.coverage_table.setColumnCount(2)
        self.coverage_table.setHorizontalHeaderLabels(
            ["File", f"Coverage (of {cov.dictionary_size})"]
        )
        self.coverage_table.setRowCount(len(files))
        for row, path in enumerate(files):
            self.coverage_table.setItem(row, 0, QTableWidgetItem(path.name))
            covered = cov.per_file_covered[path]
            self.coverage_table.setItem(
                row,
                1,
                QTableWidgetItem(f"{percents[path]:.1f}%  ({covered}/{cov.dictionary_size})"),
            )
        self.coverage_table.resizeColumnsToContents()
        self.coverage_summary.setText(f"mean coverage: {cov.mean_coverage:.1f}%")

    # --- export --------------------------------------------------------------

    def _export_csv(self) -> None:
        if self.counts_table.rowCount() == 0 and self.coverage_table.rowCount() == 0:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Export CSV", "analysis.csv", "CSV (*.csv)")
        if not path:
            return
        with Path(path).open("w", newline="", encoding="utf-8") as fh:
            writer = csv.writer(fh)
            writer.writerow(["# Counts"])
            _dump_table(writer, self.counts_table)
            writer.writerow([self.counts_summary.text()])
            writer.writerow([])
            writer.writerow(["# Coverage"])
            _dump_table(writer, self.coverage_table)
            writer.writerow([self.coverage_summary.text()])


def _dump_table(writer, table: QTableWidget) -> None:
    headers = [table.horizontalHeaderItem(c).text() for c in range(table.columnCount())]
    writer.writerow(headers)
    for row in range(table.rowCount()):
        writer.writerow(
            [
                table.item(row, c).text() if table.item(row, c) else ""
                for c in range(table.columnCount())
            ]
        )
