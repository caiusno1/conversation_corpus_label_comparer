"""Reusable visual builder for AND / OR / NOT query expressions.

Used by the Query view and by the Transitions view's compound mode.  The tree
mirrors the expression AST; :meth:`QueryBuilderWidget.root_group` converts it
into :class:`cclc.core.query.Group` / :class:`~cclc.core.query.Term` nodes.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMessageBox,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from cclc.core.query import RELATIONS, Group, Term

NODE_ROLE = Qt.UserRole + 1


class TermDialog(QDialog):
    """Edit a single term: tier, label, NOT flag and the ALL (free) flag."""

    def __init__(self, parent, tiers: list[str], label_provider, term: Term | None):
        super().__init__(parent)
        self.setWindowTitle("Term")
        self._label_provider = label_provider
        form = QFormLayout(self)
        self.tier = QComboBox()
        self.tier.addItems(tiers)
        self.label = QComboBox()
        self.label.setEditable(True)
        self.free = QCheckBox("ALL — match any label (free variable)")
        self.free.toggled.connect(lambda checked: self.label.setEnabled(not checked))
        self.negated = QCheckBox("NOT")
        if term is not None:
            self.tier.setCurrentText(term.tier)
            self.negated.setChecked(term.negated)
            self.free.setChecked(term.free)
        self.tier.currentTextChanged.connect(self._refresh_labels)
        self._refresh_labels()
        if term is not None:
            self.label.setCurrentText(term.label)
        form.addRow("Tier:", self.tier)
        form.addRow("Label:", self.label)
        form.addRow("", self.free)
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
        free = self.free.isChecked()
        return Term(
            self.tier.currentText(),
            "" if free else self.label.currentText(),
            self.negated.isChecked(),
            free=free,
        )


class QueryBuilderWidget(QWidget):
    """Tree-based editor for one boolean expression over (tier, label) terms."""

    changed = Signal()

    def __init__(
        self,
        tier_provider: Callable[[], list[str]],
        label_provider: Callable[[str], list[str]],
        title: str | None = None,
    ) -> None:
        super().__init__()
        self._tier_provider = tier_provider
        self._label_provider = label_provider

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        if title:
            layout.addWidget(QLabel(title))
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Expression"])
        self.tree.itemDoubleClicked.connect(self._edit_term)
        layout.addWidget(self.tree)

        btn_row = QHBoxLayout()
        for label, slot in (
            ("+ AND", lambda: self._add_group("AND")),
            ("+ OR", lambda: self._add_group("OR")),
            ("+ Term", self._add_term),
            ("NOT", self._toggle_not),
            ("Relation", self._set_relation),
            ("Delete", self._delete_node),
        ):
            button = QPushButton(label)
            button.clicked.connect(slot)
            btn_row.addWidget(button)
        btn_row.addStretch(1)
        layout.addLayout(btn_row)

        self.reset()

    # --- tree management -------------------------------------------------------

    def reset(self) -> None:
        self.tree.clear()
        root = QTreeWidgetItem()
        root.setData(
            0, NODE_ROLE, {"kind": "group", "op": "AND", "negated": False, "relation": None}
        )
        self.tree.addTopLevelItem(root)
        self.refresh_item_label(root)
        root.setExpanded(True)
        self.changed.emit()

    def _selected_or_root(self) -> QTreeWidgetItem:
        items = self.tree.selectedItems()
        return items[0] if items else self.tree.topLevelItem(0)

    def _group_target(self, item: QTreeWidgetItem) -> QTreeWidgetItem:
        data = item.data(0, NODE_ROLE)
        if data["kind"] == "group":
            return item
        return item.parent() or self.tree.topLevelItem(0)

    def _add_group(self, op: str) -> None:
        target = self._group_target(self._selected_or_root())
        child = QTreeWidgetItem()
        child.setData(
            0, NODE_ROLE, {"kind": "group", "op": op, "negated": False, "relation": None}
        )
        target.addChild(child)
        target.setExpanded(True)
        self.refresh_item_label(child)
        self.changed.emit()

    def _add_term(self) -> None:
        tiers = self._tier_provider()
        if not tiers:
            QMessageBox.information(self, "Term", "Add files to the selected corpus first.")
            return
        dlg = TermDialog(self, tiers, self._label_provider, None)
        if dlg.exec() != QDialog.Accepted:
            return
        self.add_term(dlg.term())

    def add_term(self, term: Term) -> None:
        """Append ``term`` to the selected group (programmatic entry point)."""
        target = self._group_target(self._selected_or_root())
        item = QTreeWidgetItem()
        item.setData(0, NODE_ROLE, {"kind": "term", "term": term})
        target.addChild(item)
        target.setExpanded(True)
        self.refresh_item_label(item)
        self.changed.emit()

    def _edit_term(self, item: QTreeWidgetItem) -> None:
        data = item.data(0, NODE_ROLE)
        if data["kind"] != "term":
            return
        dlg = TermDialog(self, self._tier_provider(), self._label_provider, data["term"])
        if dlg.exec() == QDialog.Accepted:
            data["term"] = dlg.term()
            item.setData(0, NODE_ROLE, data)
            self.refresh_item_label(item)
            self.changed.emit()

    def _toggle_not(self) -> None:
        item = self._selected_or_root()
        data = item.data(0, NODE_ROLE)
        if data["kind"] == "term":
            data["term"].negated = not data["term"].negated
        else:
            data["negated"] = not data["negated"]
        item.setData(0, NODE_ROLE, data)
        self.refresh_item_label(item)
        self.changed.emit()

    def _set_relation(self) -> None:
        item = self._selected_or_root()
        data = item.data(0, NODE_ROLE)
        if data["kind"] != "group" or data["op"] != "AND":
            QMessageBox.information(self, "Relation", "Relations apply to AND groups only.")
            return
        options = ["(distance)", *RELATIONS]
        choice, ok = QInputDialog.getItem(
            self, "Interval relation", "Relation:", options, editable=False
        )
        if ok:
            data["relation"] = None if choice == "(distance)" else choice
            item.setData(0, NODE_ROLE, data)
            self.refresh_item_label(item)
            self.changed.emit()

    def _delete_node(self) -> None:
        item = self._selected_or_root()
        if item is self.tree.topLevelItem(0):
            QMessageBox.information(self, "Delete", "The root group cannot be deleted.")
            return
        item.parent().removeChild(item)
        self.changed.emit()

    def refresh_item_label(self, item: QTreeWidgetItem) -> None:
        data = item.data(0, NODE_ROLE)
        if data["kind"] == "term":
            term: Term = data["term"]
            prefix = "NOT " if term.negated else ""
            if term.free:
                item.setText(0, f"{prefix}ALL {term.tier}")
            else:
                item.setText(0, f"{prefix}{term.tier} = “{term.label}”")
        else:
            label = "ALL of" if data["op"] == "AND" else "ANY of"
            if data["negated"]:
                label = "NOT " + label
            if data.get("relation"):
                label += f"  [{data['relation']}]"
            item.setText(0, label)

    # --- AST conversion ----------------------------------------------------------

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

    def root_group(self) -> Group:
        root_item = self.tree.topLevelItem(0)
        root = self._build_node(root_item)
        if not isinstance(root, Group):
            root = Group("AND", [root])
        return root

    def query_tiers(self) -> set[str]:
        tiers: set[str] = set()

        def walk(item: QTreeWidgetItem) -> None:
            data = item.data(0, NODE_ROLE)
            if data["kind"] == "term":
                tiers.add(data["term"].tier)
            for i in range(item.childCount()):
                walk(item.child(i))

        root = self.tree.topLevelItem(0)
        if root:
            walk(root)
        return tiers

    def render_expression(self) -> str:
        root = self.tree.topLevelItem(0)
        return self._render_node(root) if root else ""

    def _render_node(self, item: QTreeWidgetItem) -> str:
        data = item.data(0, NODE_ROLE)
        if data["kind"] == "term":
            term: Term = data["term"]
            body = f"ALL {term.tier}" if term.free else term.label
            return f"{'NOT ' if term.negated else ''}{body}"
        joiner = " AND " if data["op"] == "AND" else " OR "
        parts = [self._render_node(item.child(i)) for i in range(item.childCount())]
        inner = joiner.join(parts) if parts else "∅"
        text = f"({inner})"
        if data["negated"]:
            text = "NOT " + text
        if data.get("relation"):
            text += f" [{data['relation']}]"
        return text
