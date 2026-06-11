"""View 3 - Query: visual query builder, instance browser and statistics."""

from __future__ import annotations

import csv
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from cclc.core import analysis
from cclc.core.analysis import MissingTierError
from cclc.core.query import (
    RELATIONS,
    Group,
    Instance,
    Query,
    Term,
    evaluate,
    instance_statistics,
)

from .controller import ProjectController
from .timeline import Box, TimelineWidget

NODE_ROLE = Qt.UserRole + 1


class TermDialog(QDialog):
    """Edit a single term: tier, label and NOT flag."""

    def __init__(self, parent, tiers: list[str], label_provider, term: Term | None):
        super().__init__(parent)
        self.setWindowTitle("Term")
        self._label_provider = label_provider
        form = QFormLayout(self)
        self.tier = QComboBox()
        self.tier.addItems(tiers)
        self.label = QComboBox()
        self.label.setEditable(True)
        self.negated = QCheckBox("NOT")
        if term is not None:
            self.tier.setCurrentText(term.tier)
            self.negated.setChecked(term.negated)
        self.tier.currentTextChanged.connect(self._refresh_labels)
        self._refresh_labels()
        if term is not None:
            self.label.setCurrentText(term.label)
        form.addRow("Tier:", self.tier)
        form.addRow("Label:", self.label)
        form.addRow("", self.negated)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def _refresh_labels(self) -> None:
        current = self.label.currentText()
        self.label.clear()
        self.label.addItems(self._label_provider(self.tier.currentText()))
        if current:
            self.label.setCurrentText(current)

    def term(self) -> Term:
        return Term(self.tier.currentText(), self.label.currentText(), self.negated.isChecked())


class QueryView(QWidget):
    def __init__(self, controller: ProjectController) -> None:
        super().__init__()
        self.controller = controller
        self.controller.changed.connect(self._reload_corpora)
        self._instances: list[Instance] = []

        outer = QVBoxLayout(self)

        # --- parameters ---
        params = QHBoxLayout()
        self.corpus_combo = QComboBox()
        self.distance = QSpinBox()
        self.distance.setRange(0, 600000)
        self.distance.setValue(2000)
        self.distance.setSuffix(" ms")
        self.ref_point = QComboBox()
        self.ref_point.addItems(["begin", "mid", "end"])
        self.counting = QComboBox()
        self.counting.addItems(["anchor", "combinations"])
        params.addWidget(QLabel("Corpus:"))
        params.addWidget(self.corpus_combo)
        params.addWidget(QLabel("Max distance:"))
        params.addWidget(self.distance)
        params.addWidget(QLabel("at"))
        params.addWidget(self.ref_point)
        params.addWidget(QLabel("counting:"))
        params.addWidget(self.counting)
        params.addStretch(1)
        outer.addLayout(params)

        self.distance.valueChanged.connect(self._update_expression)
        self.ref_point.currentTextChanged.connect(self._update_expression)
        self.corpus_combo.currentTextChanged.connect(self._update_expression)

        splitter = QSplitter(Qt.Vertical)
        outer.addWidget(splitter, 1)

        # --- query builder ---
        builder_box = QGroupBox("Query builder")
        builder_layout = QVBoxLayout(builder_box)
        self.builder = QTreeWidget()
        self.builder.setHeaderLabels(["Expression"])
        builder_layout.addWidget(self.builder)
        self.builder.itemDoubleClicked.connect(self._edit_term)

        btn_row = QHBoxLayout()
        for label, slot in (
            ("+ AND group", lambda: self._add_group("AND")),
            ("+ OR group", lambda: self._add_group("OR")),
            ("+ Term", self._add_term),
            ("Toggle NOT", self._toggle_not),
            ("Set relation", self._set_relation),
            ("Delete", self._delete_node),
        ):
            b = QPushButton(label)
            b.clicked.connect(slot)
            btn_row.addWidget(b)
        builder_layout.addLayout(btn_row)
        self.expr_label = QLabel("")
        self.expr_label.setWordWrap(True)
        self.expr_label.setStyleSheet("font-style: italic; color: #225;")
        builder_layout.addWidget(self.expr_label)
        run = QPushButton("Run query")
        run.clicked.connect(self._run)
        builder_layout.addWidget(run)
        self.run_status = QLabel("")
        builder_layout.addWidget(self.run_status)
        splitter.addWidget(builder_box)

        # --- instances + timeline ---
        inst_box = QGroupBox("Instances")
        inst_layout = QVBoxLayout(inst_box)
        nav = QHBoxLayout()
        self.prev_btn = QPushButton("◀ Prev")
        self.next_btn = QPushButton("Next ▶")
        self.position = QLabel("0 / 0")
        self.prev_btn.clicked.connect(lambda: self._step(-1))
        self.next_btn.clicked.connect(lambda: self._step(1))
        sel_all = QPushButton("Select all")
        desel_all = QPushButton("Deselect all")
        sel_all.clicked.connect(lambda: self._set_all_checked(True))
        desel_all.clicked.connect(lambda: self._set_all_checked(False))
        nav.addWidget(self.prev_btn)
        nav.addWidget(self.position)
        nav.addWidget(self.next_btn)
        nav.addStretch(1)
        nav.addWidget(sel_all)
        nav.addWidget(desel_all)
        inst_layout.addLayout(nav)

        self.inst_table = QTableWidget()
        self.inst_table.itemSelectionChanged.connect(self._on_instance_selected)
        inst_layout.addWidget(self.inst_table, 1)

        self.timeline = TimelineWidget()
        tl_scroll = QScrollArea()
        tl_scroll.setWidgetResizable(True)
        tl_scroll.setWidget(self.timeline)
        inst_layout.addWidget(tl_scroll, 1)

        self.visible_tiers_box = QHBoxLayout()
        inst_layout.addWidget(QLabel("Visible tiers:"))
        vt_container = QWidget()
        vt_container.setLayout(self.visible_tiers_box)
        inst_layout.addWidget(vt_container)
        self._tier_checks: dict[str, QCheckBox] = {}

        # --- statistics hand-off ---
        stats_row = QHBoxLayout()
        self.breakdown_check = QCheckBox("Breakdown by label combination")
        use_btn = QPushButton("Use selected instances → statistics")
        use_btn.clicked.connect(self._compute_statistics)
        export_btn = QPushButton("Export stats CSV…")
        export_btn.clicked.connect(self._export_stats)
        stats_row.addWidget(self.breakdown_check)
        stats_row.addWidget(use_btn)
        stats_row.addWidget(export_btn)
        stats_row.addStretch(1)
        inst_layout.addLayout(stats_row)
        self.stats_table = QTableWidget()
        inst_layout.addWidget(self.stats_table, 1)
        self.stats_summary = QLabel("")
        inst_layout.addWidget(self.stats_summary)
        splitter.addWidget(inst_box)

        self._init_builder_root()
        self._reload_corpora()

    # --- builder model -------------------------------------------------------

    def _init_builder_root(self) -> None:
        self.builder.clear()
        root = QTreeWidgetItem()
        root.setData(
            0, NODE_ROLE, {"kind": "group", "op": "AND", "negated": False, "relation": None}
        )
        self.builder.addTopLevelItem(root)
        self._refresh_builder_labels(root)
        root.setExpanded(True)
        self._update_expression()

    def _selected_or_root(self) -> QTreeWidgetItem:
        items = self.builder.selectedItems()
        return items[0] if items else self.builder.topLevelItem(0)

    def _group_target(self, item: QTreeWidgetItem) -> QTreeWidgetItem:
        data = item.data(0, NODE_ROLE)
        if data["kind"] == "group":
            return item
        return item.parent() or self.builder.topLevelItem(0)

    def _add_group(self, op: str) -> None:
        target = self._group_target(self._selected_or_root())
        child = QTreeWidgetItem()
        child.setData(0, NODE_ROLE, {"kind": "group", "op": op, "negated": False, "relation": None})
        target.addChild(child)
        target.setExpanded(True)
        self._refresh_builder_labels(child)
        self._update_expression()

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

    def _add_term(self) -> None:
        tiers = self._all_tier_names()
        if not tiers:
            QMessageBox.information(self, "Term", "Add files to the selected corpus first.")
            return
        dlg = TermDialog(self, tiers, self._labels_for, None)
        if dlg.exec() != QDialog.Accepted:
            return
        target = self._group_target(self._selected_or_root())
        item = QTreeWidgetItem()
        item.setData(0, NODE_ROLE, {"kind": "term", "term": dlg.term()})
        target.addChild(item)
        target.setExpanded(True)
        self._refresh_builder_labels(item)
        self._update_expression()

    def _edit_term(self, item: QTreeWidgetItem) -> None:
        data = item.data(0, NODE_ROLE)
        if data["kind"] != "term":
            return
        dlg = TermDialog(self, self._all_tier_names(), self._labels_for, data["term"])
        if dlg.exec() == QDialog.Accepted:
            data["term"] = dlg.term()
            item.setData(0, NODE_ROLE, data)
            self._refresh_builder_labels(item)
            self._update_expression()

    def _toggle_not(self) -> None:
        item = self._selected_or_root()
        data = item.data(0, NODE_ROLE)
        if data["kind"] == "term":
            data["term"].negated = not data["term"].negated
        else:
            data["negated"] = not data["negated"]
        item.setData(0, NODE_ROLE, data)
        self._refresh_builder_labels(item)
        self._update_expression()

    def _set_relation(self) -> None:
        item = self._selected_or_root()
        data = item.data(0, NODE_ROLE)
        if data["kind"] != "group" or data["op"] != "AND":
            QMessageBox.information(self, "Relation", "Relations apply to AND groups only.")
            return
        options = ["(distance)", *RELATIONS]
        from PySide6.QtWidgets import QInputDialog

        choice, ok = QInputDialog.getItem(
            self, "Interval relation", "Relation:", options, editable=False
        )
        if ok:
            data["relation"] = None if choice == "(distance)" else choice
            item.setData(0, NODE_ROLE, data)
            self._refresh_builder_labels(item)
            self._update_expression()

    def _delete_node(self) -> None:
        item = self._selected_or_root()
        if item is self.builder.topLevelItem(0):
            QMessageBox.information(self, "Delete", "The root group cannot be deleted.")
            return
        item.parent().removeChild(item)
        self._update_expression()

    def _refresh_builder_labels(self, item: QTreeWidgetItem) -> None:
        data = item.data(0, NODE_ROLE)
        if data["kind"] == "term":
            term: Term = data["term"]
            prefix = "NOT " if term.negated else ""
            item.setText(0, f"{prefix}{term.tier} = “{term.label}”")
        else:
            label = "ALL of" if data["op"] == "AND" else "ANY of"
            if data["negated"]:
                label = "NOT " + label
            if data.get("relation"):
                label += f"  [{data['relation']}]"
            item.setText(0, label)

    # --- build Query from the tree ------------------------------------------

    def _build_node(self, item: QTreeWidgetItem):
        data = item.data(0, NODE_ROLE)
        if data["kind"] == "term":
            return data["term"]
        children = [self._build_node(item.child(i)) for i in range(item.childCount())]
        return Group(
            op=data["op"],
            children=children,
            negated=data["negated"],
            relation=data.get("relation"),
        )

    def _build_query(self) -> Query | None:
        root_item = self.builder.topLevelItem(0)
        root = self._build_node(root_item)
        if not isinstance(root, Group):
            root = Group("AND", [root])
        return Query(
            root=root,
            max_distance_ms=self.distance.value(),
            reference_point=self.ref_point.currentText(),
            counting_mode=self.counting.currentText(),
        )

    def _render_node(self, item: QTreeWidgetItem) -> str:
        data = item.data(0, NODE_ROLE)
        if data["kind"] == "term":
            term: Term = data["term"]
            return f"{'NOT ' if term.negated else ''}{term.label}"
        joiner = " AND " if data["op"] == "AND" else " OR "
        parts = [self._render_node(item.child(i)) for i in range(item.childCount())]
        inner = joiner.join(parts) if parts else "∅"
        text = f"({inner})"
        if data["negated"]:
            text = "NOT " + text
        if data.get("relation"):
            text += f" [{data['relation']}]"
        return text

    def _update_expression(self) -> None:
        root = self.builder.topLevelItem(0)
        expr = self._render_node(root) if root else ""
        tail = f", within {self.distance.value()} ms at {self.ref_point.currentText()}"
        self.expr_label.setText("≙ " + expr + tail)

    # --- corpora -------------------------------------------------------------

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
        self._update_expression()

    # --- run -----------------------------------------------------------------

    def _run(self) -> None:
        self.run_status.setText("")
        corpus = self._current_corpus()
        if corpus is None or not corpus.files:
            self.run_status.setText("Select a non-empty corpus.")
            return
        query = self._build_query()
        try:
            query.validate()
            self._instances = evaluate(self.controller.project, corpus, query)
        except MissingTierError as exc:
            self.run_status.setText(str(exc))
            return
        except ValueError as exc:
            self.run_status.setText(f"Invalid query: {exc}")
            return
        except Exception as exc:  # noqa: BLE001
            self.run_status.setText(f"Query failed: {exc}")
            return

        files = {inst.file for inst in self._instances}
        self.run_status.setText(
            f"{len(self._instances)} instances in {len(files)} file(s)"
        )
        self._populate_instances()
        self._populate_visible_tiers()

    def _populate_instances(self) -> None:
        self.inst_table.clear()
        self.inst_table.setColumnCount(4)
        self.inst_table.setHorizontalHeaderLabels(["Use", "File", "Time", "Matched"])
        self.inst_table.setRowCount(len(self._instances))
        for row, inst in enumerate(self._instances):
            check = QTableWidgetItem()
            check.setFlags(check.flags() | Qt.ItemIsUserCheckable)
            check.setCheckState(Qt.Checked)
            self.inst_table.setItem(row, 0, check)
            self.inst_table.setItem(row, 1, QTableWidgetItem(inst.file.name))
            self.inst_table.setItem(
                row, 2, QTableWidgetItem(f"{inst.start_ms}–{inst.end_ms} ms")
            )
            labels = ", ".join(f"{k}" for k in sorted(inst.matched.keys()))
            self.inst_table.setItem(row, 3, QTableWidgetItem(labels))
        self.inst_table.resizeColumnsToContents()
        if self._instances:
            self.inst_table.selectRow(0)
        self._update_position()

    def _populate_visible_tiers(self) -> None:
        while self.visible_tiers_box.count():
            item = self.visible_tiers_box.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._tier_checks.clear()
        query_tiers = self._query_tiers()
        for tier in self._all_tier_names():
            check = QCheckBox(tier)
            check.setChecked(tier in query_tiers)
            check.toggled.connect(self._on_instance_selected)
            self.visible_tiers_box.addWidget(check)
            self._tier_checks[tier] = check
        self.visible_tiers_box.addStretch(1)

    def _query_tiers(self) -> set[str]:
        tiers: set[str] = set()

        def walk(item: QTreeWidgetItem):
            data = item.data(0, NODE_ROLE)
            if data["kind"] == "term":
                tiers.add(data["term"].tier)
            for i in range(item.childCount()):
                walk(item.child(i))

        root = self.builder.topLevelItem(0)
        if root:
            walk(root)
        return tiers

    # --- navigation & timeline ----------------------------------------------

    def _step(self, delta: int) -> None:
        if not self._instances:
            return
        row = self.inst_table.currentRow()
        row = max(0, min(len(self._instances) - 1, row + delta))
        self.inst_table.selectRow(row)

    def _update_position(self) -> None:
        total = len(self._instances)
        row = self.inst_table.currentRow()
        self.position.setText(f"{row + 1 if row >= 0 else 0} / {total}")

    def _on_instance_selected(self) -> None:
        self._update_position()
        row = self.inst_table.currentRow()
        if row < 0 or row >= len(self._instances):
            self.timeline.clear()
            return
        inst = self._instances[row]
        visible = [t for t, c in self._tier_checks.items() if c.isChecked()]
        if not visible:
            visible = sorted({a_key.split("=")[0] for a_key in inst.matched})
        try:
            doc = self.controller.project.document(inst.file)
        except Exception:  # noqa: BLE001
            self.timeline.clear()
            return

        pad = self.timeline.PAD_MS
        lo = max(0, inst.start_ms - pad)
        hi = inst.end_ms + pad
        matched_ids = {(a.value, a.start_ms, a.end_ms) for a in inst.matched.values()}
        boxes: list[Box] = []
        for tier in visible:
            tobj = doc.tiers.get(tier)
            if tobj is None:
                continue
            for ann in tobj.annotations:
                if ann.start_ms is None or ann.end_ms is None:
                    continue
                if ann.end_ms < lo or ann.start_ms > hi:
                    continue
                highlight = (ann.value, ann.start_ms, ann.end_ms) in matched_ids
                boxes.append(Box(tier, ann.value, ann.start_ms, ann.end_ms, highlight))
        self.timeline.set_data(visible, boxes, (lo, hi))

    def _set_all_checked(self, checked: bool) -> None:
        state = Qt.Checked if checked else Qt.Unchecked
        for row in range(self.inst_table.rowCount()):
            self.inst_table.item(row, 0).setCheckState(state)

    def _selected_instances(self) -> list[Instance]:
        out = []
        for row in range(self.inst_table.rowCount()):
            if self.inst_table.item(row, 0).checkState() == Qt.Checked:
                out.append(self._instances[row])
        return out

    # --- statistics ----------------------------------------------------------

    def _compute_statistics(self) -> None:
        corpus = self._current_corpus()
        if corpus is None:
            return
        selected = self._selected_instances()
        breakdown = self.breakdown_check.isChecked()
        stats = instance_statistics(corpus, selected, breakdown=breakdown)

        self.stats_table.clear()
        self.stats_table.setColumnCount(2)
        self.stats_table.setHorizontalHeaderLabels(["File", "Instances"])
        files = list(stats.per_file.keys())
        self.stats_table.setRowCount(len(files))
        for row, path in enumerate(files):
            self.stats_table.setItem(row, 0, QTableWidgetItem(path.name))
            self.stats_table.setItem(row, 1, QTableWidgetItem(str(stats.per_file[path])))
        self.stats_table.resizeColumnsToContents()

        stdev = stats.stdev
        stdev_text = f"{stdev:.2f}" if stdev is not None else "n/a"
        summary = (
            f"selected: {len(selected)}   total: {stats.total}   "
            f"mean/file: {stats.mean:.2f}   σ (sample): {stdev_text}"
        )
        if breakdown and stats.combinations:
            combos = "; ".join(
                f"{' + '.join(k)}: {v}" for k, v in sorted(stats.combinations.items())
            )
            summary += f"\nCombinations — {combos}"
        self.stats_summary.setText(summary)

    def _export_stats(self) -> None:
        if self.stats_table.rowCount() == 0:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Export stats", "instances.csv", "CSV (*.csv)")
        if not path:
            return
        with Path(path).open("w", newline="", encoding="utf-8") as fh:
            writer = csv.writer(fh)
            writer.writerow(["File", "Instances"])
            for row in range(self.stats_table.rowCount()):
                writer.writerow(
                    [self.stats_table.item(row, 0).text(), self.stats_table.item(row, 1).text()]
                )
            writer.writerow([])
            for line in self.stats_summary.text().splitlines():
                writer.writerow([line])
