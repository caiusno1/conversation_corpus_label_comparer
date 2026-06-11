"""View 1 - Configuration: pick ``.eaf`` files and organise them into corpora.

Left: a filesystem tree filtered to ``*.eaf`` with drag enabled.  Right: a tree
of corpora that accepts drops from the filesystem panel, from the OS file
manager, and internal moves of file nodes between corpora.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QDir, Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFileDialog,
    QFileSystemModel,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMenu,
    QMessageBox,
    QPushButton,
    QTreeView,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .controller import ProjectController

CORPUS_ROLE = Qt.UserRole + 1
PATH_ROLE = Qt.UserRole + 2


def _collect_eaf(paths: list[Path]) -> list[Path]:
    """Expand directories to the ``.eaf`` files they contain (recursive)."""
    out: list[Path] = []
    for path in paths:
        if path.is_dir():
            out.extend(sorted(path.rglob("*.eaf")))
        elif path.suffix.lower() == ".eaf":
            out.append(path)
    return out


class CorporaTree(QTreeWidget):
    """Tree of corpora (top level) and their files (children) with drag & drop."""

    def __init__(self, view: ConfigView) -> None:
        super().__init__()
        self._view = view
        self.setHeaderLabels(["Corpora"])
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.setAcceptDrops(True)
        self.setDragEnabled(True)
        self.setDropIndicatorShown(True)
        self.setContextMenuPolicy(Qt.CustomContextMenu)

    # --- drop handling -------------------------------------------------------

    def dragEnterEvent(self, event):  # noqa: N802 (Qt naming)
        if event.mimeData().hasUrls() or event.source() is self:
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event):  # noqa: N802
        if event.mimeData().hasUrls() or event.source() is self:
            event.acceptProposedAction()
        else:
            event.ignore()

    def _target_corpus(self, item: QTreeWidgetItem | None) -> str | None:
        if item is None:
            return None
        if item.data(0, CORPUS_ROLE) is not None:
            return item.data(0, CORPUS_ROLE)
        # a file node: use its parent corpus
        parent = item.parent()
        if parent is not None:
            return parent.data(0, CORPUS_ROLE)
        return None

    def dropEvent(self, event):  # noqa: N802
        target_item = self.itemAt(event.position().toPoint())
        target_corpus = self._target_corpus(target_item)
        if target_corpus is None:
            event.ignore()
            return

        if event.mimeData().hasUrls():
            paths = [Path(u.toLocalFile()) for u in event.mimeData().urls() if u.toLocalFile()]
            files = _collect_eaf(paths)
            if files:
                self._view.controller.add_files(target_corpus, files)
            event.acceptProposedAction()
            return

        if event.source() is self:
            moves: list[tuple[str, Path]] = []
            for item in self.selectedItems():
                path = item.data(0, PATH_ROLE)
                parent = item.parent()
                if path is not None and parent is not None:
                    moves.append((parent.data(0, CORPUS_ROLE), Path(path)))
            for src, path in moves:
                self._view.controller.move_file(src, target_corpus, path)
            event.acceptProposedAction()
            return

        event.ignore()


class ConfigView(QWidget):
    def __init__(self, controller: ProjectController) -> None:
        super().__init__()
        self.controller = controller
        self.controller.changed.connect(self.refresh)

        layout = QHBoxLayout(self)

        # --- filesystem panel ---
        fs_panel = QVBoxLayout()
        fs_panel.addWidget(QLabel("Filesystem (*.eaf)"))
        self.fs_model = QFileSystemModel()
        self.fs_model.setRootPath("")
        self.fs_model.setNameFilters(["*.eaf"])
        self.fs_model.setNameFilterDisables(False)
        self.fs_model.setFilter(QDir.AllDirs | QDir.Files | QDir.NoDotAndDotDot)
        self.fs_tree = QTreeView()
        self.fs_tree.setModel(self.fs_model)
        self.fs_tree.setRootIndex(self.fs_model.index(QDir.homePath()))
        self.fs_tree.setDragEnabled(True)
        self.fs_tree.setSelectionMode(QAbstractItemView.ExtendedSelection)
        for col in range(1, self.fs_model.columnCount()):
            self.fs_tree.hideColumn(col)
        fs_panel.addWidget(self.fs_tree)
        add_btn = QPushButton("Add files…")
        add_btn.clicked.connect(self._add_files_dialog)
        fs_panel.addWidget(add_btn)
        layout.addLayout(fs_panel, 1)

        # --- corpora panel ---
        corpora_panel = QVBoxLayout()
        corpora_panel.addWidget(QLabel("Corpora (drag files here / between corpora)"))
        self.tree = CorporaTree(self)
        self.tree.customContextMenuRequested.connect(self._context_menu)
        corpora_panel.addWidget(self.tree)

        buttons = QHBoxLayout()
        for label, slot in (
            ("+ New corpus", self._new_corpus),
            ("Rename", self._rename_corpus),
            ("Remove", self._remove_corpus),
        ):
            btn = QPushButton(label)
            btn.clicked.connect(slot)
            buttons.addWidget(btn)
        corpora_panel.addLayout(buttons)
        layout.addLayout(corpora_panel, 1)

        self.refresh()

    # --- rendering -----------------------------------------------------------

    def refresh(self) -> None:
        expanded = {
            self.tree.topLevelItem(i).text(0)
            for i in range(self.tree.topLevelItemCount())
            if self.tree.topLevelItem(i).isExpanded()
        }
        self.tree.clear()
        for corpus in self.controller.project.corpora:
            top = QTreeWidgetItem([f"{corpus.name}  ({len(corpus.files)} files)"])
            top.setData(0, CORPUS_ROLE, corpus.name)
            top.setFlags(top.flags() & ~Qt.ItemIsDragEnabled)
            for path in corpus.files:
                child = QTreeWidgetItem([path.name])
                child.setData(0, PATH_ROLE, str(path))
                child.setToolTip(0, str(path))
                child.setFlags(child.flags() & ~Qt.ItemIsDropEnabled)
                top.addChild(child)
            self.tree.addTopLevelItem(top)
            if not expanded or corpus.name in expanded:
                top.setExpanded(True)

    # --- corpus management ---------------------------------------------------

    def _new_corpus(self) -> None:
        self.controller.add_corpus()

    def _selected_corpus_name(self) -> str | None:
        items = self.tree.selectedItems()
        if not items:
            return None
        item = items[0]
        if item.data(0, CORPUS_ROLE) is not None:
            return item.data(0, CORPUS_ROLE)
        parent = item.parent()
        return parent.data(0, CORPUS_ROLE) if parent else None

    def _rename_corpus(self) -> None:
        name = self._selected_corpus_name()
        if name is None:
            return
        new, ok = QInputDialog.getText(self, "Rename corpus", "New name:", text=name)
        if ok and new and new != name:
            if not self.controller.rename_corpus(name, new):
                QMessageBox.warning(self, "Rename", f"A corpus named {new!r} already exists.")

    def _remove_corpus(self) -> None:
        name = self._selected_corpus_name()
        if name is None:
            return
        confirm = QMessageBox.question(
            self, "Remove corpus", f"Remove corpus {name!r}? (Files on disk are untouched.)"
        )
        if confirm == QMessageBox.Yes:
            self.controller.remove_corpus(name)

    def _add_files_dialog(self) -> None:
        if not self.controller.project.corpora:
            self.controller.add_corpus()
        target = self._selected_corpus_name() or self.controller.project.corpora[0].name
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Add ELAN files", QDir.homePath(), "ELAN files (*.eaf)"
        )
        if paths:
            self.controller.add_files(target, [Path(p) for p in paths])

    # --- context menu --------------------------------------------------------

    def _context_menu(self, pos) -> None:
        item = self.tree.itemAt(pos)
        if item is None:
            return
        menu = QMenu(self)
        path = item.data(0, PATH_ROLE)
        if path is not None:
            parent_corpus = item.parent().data(0, CORPUS_ROLE)
            move_menu = menu.addMenu("Move to corpus")
            for corpus in self.controller.project.corpora:
                if corpus.name == parent_corpus:
                    continue
                act = move_menu.addAction(corpus.name)
                act.triggered.connect(
                    lambda _=False, c=corpus.name, p=path: self.controller.move_file(
                        parent_corpus, c, Path(p)
                    )
                )
            remove = menu.addAction("Remove from corpus")
            remove.triggered.connect(
                lambda: self.controller.remove_file(parent_corpus, Path(path))
            )
        else:
            menu.addAction("Rename", self._rename_corpus)
            menu.addAction("Remove", self._remove_corpus)
        menu.exec(self.tree.viewport().mapToGlobal(pos))
