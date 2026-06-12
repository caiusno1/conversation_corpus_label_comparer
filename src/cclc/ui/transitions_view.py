"""View 5 - Transitions: label-to-label transition matrices.

Cell (row *i*, column *j*) shows how often label *i* appeared immediately after
label *j*, divided by all instances of label *i*.  Several dictionary tiers can
be selected at once: their dictionaries are united and their annotations merged
into one sequence, making cross-tier transitions visible.  A scope selector
switches between the whole corpus and a single file.
"""

from __future__ import annotations

import csv
from pathlib import Path

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from cclc.core import analysis, transitions
from cclc.core.analysis import MissingTierError

from .controller import ProjectController


class TransitionsView(QWidget):
    def __init__(self, controller: ProjectController) -> None:
        super().__init__()
        self.controller = controller
        self.controller.changed.connect(self._reload_corpora)
        self._tier_checks: dict[str, QCheckBox] = {}

        layout = QVBoxLayout(self)

        # --- selection row ---
        form = QHBoxLayout()
        self.corpus_combo = QComboBox()
        self.scope_combo = QComboBox()
        self.case_check = QCheckBox("Case-sensitive")
        self.case_check.setChecked(True)
        self.raw_check = QCheckBox("Show raw counts")
        form.addWidget(QLabel("Corpus:"))
        form.addWidget(self.corpus_combo)
        form.addWidget(QLabel("Scope:"))
        form.addWidget(self.scope_combo)
        form.addWidget(self.case_check)
        form.addWidget(self.raw_check)
        form.addStretch(1)
        layout.addLayout(form)

        # --- tier checklist ---
        tier_row = QHBoxLayout()
        tier_row.addWidget(QLabel("Tiers:"))
        self.tiers_box = QHBoxLayout()
        tier_row.addLayout(self.tiers_box)
        tier_row.addStretch(1)
        layout.addLayout(tier_row)

        definition = QLabel(
            "Cell (row i, column j): how often label i appeared immediately after "
            "label j, divided by all instances of label i. Row headers show the "
            "denominator. Selecting several tiers merges their annotations into one "
            "sequence over the union of their dictionaries."
        )
        definition.setWordWrap(True)
        definition.setStyleSheet("color: #555;")
        layout.addWidget(definition)

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
        self.scope_combo.currentIndexChanged.connect(self._recompute)
        self.case_check.toggled.connect(self._recompute)
        self.raw_check.toggled.connect(self._recompute)

        self._reload_corpora()

    # --- population ------------------------------------------------------------

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

        # tier checklist
        previously = {t for t, c in self._tier_checks.items() if c.isChecked()}
        while self.tiers_box.count():
            item = self.tiers_box.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._tier_checks.clear()
        tiers: list[str] = []
        if corpus is not None and corpus.files:
            try:
                tiers = analysis.dictionary_tiers(self.controller.project, corpus)
            except Exception:  # noqa: BLE001
                tiers = []
        for i, tier in enumerate(tiers):
            check = QCheckBox(tier)
            check.setChecked(tier in previously or (not previously and i == 0))
            check.toggled.connect(self._recompute)
            self.tiers_box.addWidget(check)
            self._tier_checks[tier] = check

        # scope combo: whole corpus + every file
        current_scope = self.scope_combo.currentData()
        self.scope_combo.blockSignals(True)
        self.scope_combo.clear()
        self.scope_combo.addItem("— whole corpus —", None)
        if corpus is not None:
            for path in corpus.files:
                self.scope_combo.addItem(path.name, str(path))
        if current_scope is not None:
            idx = self.scope_combo.findData(current_scope)
            if idx >= 0:
                self.scope_combo.setCurrentIndex(idx)
        self.scope_combo.blockSignals(False)

        self._recompute()

    def _selected_tiers(self) -> list[str]:
        return [t for t, c in self._tier_checks.items() if c.isChecked()]

    # --- computation -------------------------------------------------------------

    def _recompute(self) -> None:
        self.status.setText("")
        self.table.clear()
        self.table.setRowCount(0)
        self.table.setColumnCount(0)
        self.summary.setText("")

        corpus = self._current_corpus()
        tiers = self._selected_tiers()
        if corpus is None or not corpus.files or not tiers:
            return
        scope = self.scope_combo.currentData()
        scope_file = Path(scope) if scope else None

        try:
            result = transitions.transition_matrix(
                self.controller.project,
                corpus,
                tiers,
                scope_file=scope_file,
                case_sensitive=self.case_check.isChecked(),
            )
        except MissingTierError as exc:
            self.status.setText(str(exc))
            return
        except Exception as exc:  # noqa: BLE001
            self.status.setText(f"Could not analyse: {exc}")
            return

        labels = result.labels
        raw = self.raw_check.isChecked()
        self.table.setRowCount(len(labels))
        self.table.setColumnCount(len(labels))
        self.table.setHorizontalHeaderLabels(labels)
        self.table.setVerticalHeaderLabels(
            [f"{label}  (n={result.label_totals.get(label, 0)})" for label in labels]
        )
        for row, label_i in enumerate(labels):
            for col, label_j in enumerate(labels):
                if raw:
                    text = str(result.count(label_i, label_j))
                else:
                    ratio = result.ratio(label_i, label_j)
                    text = "—" if ratio is None else f"{ratio:.3f}"
                self.table.setItem(row, col, QTableWidgetItem(text))
        self.table.resizeColumnsToContents()

        scope_text = scope_file.name if scope_file else "whole corpus"
        self.summary.setText(
            f"{scope_text}: {result.annotations_considered} annotations, "
            f"{result.transition_total} transitions"
        )

    # --- export --------------------------------------------------------------

    def _export_csv(self) -> None:
        if self.table.rowCount() == 0:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Export CSV", "transitions.csv", "CSV (*.csv)"
        )
        if not path:
            return
        with Path(path).open("w", newline="", encoding="utf-8") as fh:
            writer = csv.writer(fh)
            headers = [
                self.table.horizontalHeaderItem(c).text()
                for c in range(self.table.columnCount())
            ]
            writer.writerow(["i \\ j", *headers])
            for row in range(self.table.rowCount()):
                writer.writerow(
                    [self.table.verticalHeaderItem(row).text()]
                    + [
                        self.table.item(row, c).text() if self.table.item(row, c) else ""
                        for c in range(self.table.columnCount())
                    ]
                )
            writer.writerow([])
            writer.writerow([self.summary.text()])
