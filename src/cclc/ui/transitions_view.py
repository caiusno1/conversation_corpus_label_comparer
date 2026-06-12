"""View 5 - Transitions: label-to-label transition matrices in three modes.

All modes share the cell definition: cell (row *i*, column *j*) shows how often
element *i* appeared immediately after element *j*, divided by all instances of
element *i* (row headers carry the denominator).

* **Merged sequence** - the annotations of the checked dictionary tiers are
  merged into one time-ordered sequence over the union of their dictionaries.
* **Tier → tier** - for every annotation on the source tier, the next
  annotation (strictly later start) on the target tier is the transition
  target; rows = target dictionary, columns = source dictionary.
* **Compound → compound** - two compounds A and B are defined with the visual
  AND/OR/NOT builder (as in the Query view); their instances form the sequence.
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
    QSpinBox,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from cclc.core import analysis, transitions
from cclc.core.analysis import MissingTierError
from cclc.core.query import Query
from cclc.core.transitions import TransitionResult

from .controller import ProjectController
from .query_builder import QueryBuilderWidget

DEFINITIONS = {
    "merged": (
        "Cell (row i, column j): how often label i appeared immediately after "
        "label j in the merged sequence of the checked tiers, divided by all "
        "instances of label i. Several tiers use the union of their dictionaries."
    ),
    "tier2tier": (
        "Cell (row i, column j): how often an annotation with label i on the "
        "target tier is the NEXT target-tier annotation (strictly later start) "
        "after an annotation with label j on the source tier, divided by all "
        "instances of label i on the target tier. Rows can sum to more than 1 "
        "when several source annotations share the same next target."
    ),
    "compound": (
        "Define compounds A and B (AND/OR/NOT + max distance, as in the Query "
        "view; the case toggle does not apply here). Their instances are ordered "
        "by start time per file, and cell (row i, column j) shows how often an "
        "instance of compound i immediately follows an instance of compound j, "
        "divided by all instances of i. Press “Run compounds” to compute."
    ),
}


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
        self.mode_combo = QComboBox()
        self.mode_combo.addItem("Merged sequence", "merged")
        self.mode_combo.addItem("Tier → tier", "tier2tier")
        self.mode_combo.addItem("Compound → compound", "compound")
        self.scope_combo = QComboBox()
        self.case_check = QCheckBox("Case-sensitive")
        self.case_check.setChecked(True)
        self.raw_check = QCheckBox("Show raw counts")
        form.addWidget(QLabel("Corpus:"))
        form.addWidget(self.corpus_combo)
        form.addWidget(QLabel("Mode:"))
        form.addWidget(self.mode_combo)
        form.addWidget(QLabel("Scope:"))
        form.addWidget(self.scope_combo)
        form.addWidget(self.case_check)
        form.addWidget(self.raw_check)
        form.addStretch(1)
        layout.addLayout(form)

        # --- per-mode controls (stacked) ---
        self.stack = QStackedWidget()
        layout.addWidget(self.stack)

        # page 0: merged sequence - tier checklist
        merged_page = QWidget()
        merged_layout = QHBoxLayout(merged_page)
        merged_layout.setContentsMargins(0, 0, 0, 0)
        merged_layout.addWidget(QLabel("Tiers:"))
        self.tiers_box = QHBoxLayout()
        merged_layout.addLayout(self.tiers_box)
        merged_layout.addStretch(1)
        self.stack.addWidget(merged_page)

        # page 1: tier -> tier - source/target combos
        t2t_page = QWidget()
        t2t_layout = QHBoxLayout(t2t_page)
        t2t_layout.setContentsMargins(0, 0, 0, 0)
        self.source_tier_combo = QComboBox()
        self.target_tier_combo = QComboBox()
        t2t_layout.addWidget(QLabel("From tier (columns j):"))
        t2t_layout.addWidget(self.source_tier_combo)
        t2t_layout.addWidget(QLabel("To tier (rows i):"))
        t2t_layout.addWidget(self.target_tier_combo)
        t2t_layout.addStretch(1)
        self.stack.addWidget(t2t_page)

        # page 2: compound -> compound - two builders + parameters + run
        compound_page = QWidget()
        compound_layout = QVBoxLayout(compound_page)
        compound_layout.setContentsMargins(0, 0, 0, 0)
        builders = QHBoxLayout()
        self.builder_a = QueryBuilderWidget(
            self._all_tier_names, self._labels_for, title="Compound A"
        )
        self.builder_b = QueryBuilderWidget(
            self._all_tier_names, self._labels_for, title="Compound B"
        )
        builders.addWidget(self.builder_a)
        builders.addWidget(self.builder_b)
        compound_layout.addLayout(builders)
        params = QHBoxLayout()
        self.distance = QSpinBox()
        self.distance.setRange(0, 600000)
        self.distance.setValue(2000)
        self.distance.setSuffix(" ms")
        self.ref_point = QComboBox()
        self.ref_point.addItems(["begin", "mid", "end"])
        run_btn = QPushButton("Run compounds")
        run_btn.clicked.connect(self._run_compounds)
        params.addWidget(QLabel("Max distance:"))
        params.addWidget(self.distance)
        params.addWidget(QLabel("at"))
        params.addWidget(self.ref_point)
        params.addWidget(run_btn)
        params.addStretch(1)
        compound_layout.addLayout(params)
        self.stack.addWidget(compound_page)

        self.definition = QLabel(DEFINITIONS["merged"])
        self.definition.setWordWrap(True)
        self.definition.setStyleSheet("color: #555;")
        layout.addWidget(self.definition)

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
        self.mode_combo.currentIndexChanged.connect(self._on_mode_changed)
        self.scope_combo.currentIndexChanged.connect(self._recompute)
        self.case_check.toggled.connect(self._recompute)
        self.raw_check.toggled.connect(self._recompute)
        self.source_tier_combo.currentTextChanged.connect(self._recompute)
        self.target_tier_combo.currentTextChanged.connect(self._recompute)

        self._reload_corpora()

    # --- providers for the compound builders -----------------------------------

    def _all_tier_names(self) -> list[str]:
        corpus = self._current_corpus()
        if corpus is None:
            return []
        names: set[str] = set()
        for path in corpus.files:
            try:
                names.update(self.controller.project.document(path).tier_names())
            except Exception:  # noqa: BLE001
                pass
        return sorted(names)

    def _labels_for(self, tier: str) -> list[str]:
        corpus = self._current_corpus()
        if corpus is None or not tier:
            return []
        try:
            return analysis.union_dictionary(self.controller.project, corpus, tier)
        except Exception:  # noqa: BLE001
            return []

    # --- population ------------------------------------------------------------

    def _mode(self) -> str:
        return self.mode_combo.currentData()

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
        tiers: list[str] = []
        if corpus is not None and corpus.files:
            try:
                tiers = analysis.dictionary_tiers(self.controller.project, corpus)
            except Exception:  # noqa: BLE001
                tiers = []

        # mode 1: tier checklist
        previously = {t for t, c in self._tier_checks.items() if c.isChecked()}
        while self.tiers_box.count():
            item = self.tiers_box.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._tier_checks.clear()
        for i, tier in enumerate(tiers):
            check = QCheckBox(tier)
            check.setChecked(tier in previously or (not previously and i == 0))
            check.toggled.connect(self._recompute)
            self.tiers_box.addWidget(check)
            self._tier_checks[tier] = check

        # mode 2: source/target combos
        for combo, default_index in ((self.source_tier_combo, 0), (self.target_tier_combo, 1)):
            current = combo.currentText()
            combo.blockSignals(True)
            combo.clear()
            combo.addItems(tiers)
            idx = combo.findText(current)
            if idx >= 0:
                combo.setCurrentIndex(idx)
            elif tiers:
                combo.setCurrentIndex(min(default_index, len(tiers) - 1))
            combo.blockSignals(False)

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

    def _on_mode_changed(self) -> None:
        mode = self._mode()
        self.stack.setCurrentIndex(self.mode_combo.currentIndex())
        self.definition.setText(DEFINITIONS[mode])
        self._recompute()

    def _selected_tiers(self) -> list[str]:
        return [t for t, c in self._tier_checks.items() if c.isChecked()]

    def _scope_file(self) -> Path | None:
        scope = self.scope_combo.currentData()
        return Path(scope) if scope else None

    # --- computation -------------------------------------------------------------

    def _clear_output(self) -> None:
        self.status.setText("")
        self.table.clear()
        self.table.setRowCount(0)
        self.table.setColumnCount(0)
        self.summary.setText("")

    def _recompute(self) -> None:
        self._clear_output()
        corpus = self._current_corpus()
        if corpus is None or not corpus.files:
            return
        mode = self._mode()
        if mode == "compound":
            self.status.setText("Press “Run compounds” to compute.")
            self.status.setStyleSheet("color: #555;")
            return
        self.status.setStyleSheet("color: #b00;")

        try:
            if mode == "merged":
                tiers = self._selected_tiers()
                if not tiers:
                    return
                result = transitions.transition_matrix(
                    self.controller.project,
                    corpus,
                    tiers,
                    scope_file=self._scope_file(),
                    case_sensitive=self.case_check.isChecked(),
                )
            else:  # tier2tier
                source = self.source_tier_combo.currentText()
                target = self.target_tier_combo.currentText()
                if not source or not target:
                    return
                result = transitions.tier_to_tier_matrix(
                    self.controller.project,
                    corpus,
                    source,
                    target,
                    scope_file=self._scope_file(),
                    case_sensitive=self.case_check.isChecked(),
                )
        except MissingTierError as exc:
            self.status.setText(str(exc))
            return
        except Exception as exc:  # noqa: BLE001
            self.status.setText(f"Could not analyse: {exc}")
            return
        self._fill_table(result, unit="annotations")

    def _run_compounds(self) -> None:
        self._clear_output()
        self.status.setStyleSheet("color: #b00;")
        corpus = self._current_corpus()
        if corpus is None or not corpus.files:
            self.status.setText("Select a non-empty corpus.")
            return
        queries = {
            "A": Query(
                root=self.builder_a.root_group(),
                max_distance_ms=self.distance.value(),
                reference_point=self.ref_point.currentText(),
            ),
            "B": Query(
                root=self.builder_b.root_group(),
                max_distance_ms=self.distance.value(),
                reference_point=self.ref_point.currentText(),
            ),
        }
        try:
            for query in queries.values():
                query.validate()
            result = transitions.compound_transition_matrix(
                self.controller.project, corpus, queries, scope_file=self._scope_file()
            )
        except MissingTierError as exc:
            self.status.setText(str(exc))
            return
        except ValueError as exc:
            self.status.setText(f"Invalid compound: {exc}")
            return
        except Exception as exc:  # noqa: BLE001
            self.status.setText(f"Could not analyse: {exc}")
            return
        self._fill_table(result, unit="instances")

    def _fill_table(self, result: TransitionResult, unit: str) -> None:
        raw = self.raw_check.isChecked()
        self.table.setRowCount(len(result.row_labels))
        self.table.setColumnCount(len(result.col_labels))
        self.table.setHorizontalHeaderLabels(result.col_labels)
        self.table.setVerticalHeaderLabels(
            [
                f"{label}  (n={result.label_totals.get(label, 0)})"
                for label in result.row_labels
            ]
        )
        for row, label_i in enumerate(result.row_labels):
            for col, label_j in enumerate(result.col_labels):
                if raw:
                    text = str(result.count(label_i, label_j))
                else:
                    ratio = result.ratio(label_i, label_j)
                    text = "—" if ratio is None else f"{ratio:.3f}"
                self.table.setItem(row, col, QTableWidgetItem(text))
        self.table.resizeColumnsToContents()

        scope_file = self._scope_file()
        scope_text = scope_file.name if scope_file else "whole corpus"
        self.summary.setText(
            f"{scope_text}: {result.annotations_considered} {unit}, "
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
