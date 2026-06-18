"""The application's main window: three tabs plus project save/open."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QFileDialog,
    QMainWindow,
    QMessageBox,
    QTabWidget,
)

from cclc.core.corpus import CorpusProject

from .analysis_view import AnalysisView
from .config_view import ConfigView
from .controller import ProjectController
from .interval_view import IntervalView
from .query_view import QueryView
from .transitions_view import TransitionsView


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("ELAN Corpus Label Comparer")
        self.resize(1100, 760)
        self.controller = ProjectController()

        tabs = QTabWidget()
        tabs.addTab(ConfigView(self.controller), "Corpora")
        tabs.addTab(AnalysisView(self.controller), "Analysis")
        tabs.addTab(IntervalView(self.controller), "Interval")
        tabs.addTab(TransitionsView(self.controller), "Transitions")
        tabs.addTab(QueryView(self.controller), "Query")
        self.setCentralWidget(tabs)

        self._build_menu()
        self.controller.changed.connect(self._update_title)
        self._update_title()

    def _build_menu(self) -> None:
        file_menu = self.menuBar().addMenu("&File")
        for text, slot, shortcut in (
            ("&New project", self._new_project, "Ctrl+N"),
            ("&Open project…", self._open_project, "Ctrl+O"),
            ("&Save project", self._save_project, "Ctrl+S"),
            ("Save project &as…", self._save_project_as, "Ctrl+Shift+S"),
        ):
            action = QAction(text, self)
            action.setShortcut(shortcut)
            action.triggered.connect(slot)
            file_menu.addAction(action)
        file_menu.addSeparator()
        quit_action = QAction("&Quit", self)
        quit_action.setShortcut("Ctrl+Q")
        quit_action.triggered.connect(self.close)
        file_menu.addAction(quit_action)

    def _update_title(self) -> None:
        name = self.controller.project_path.name if self.controller.project_path else "untitled"
        self.setWindowTitle(f"ELAN Corpus Label Comparer — {name}")

    def _new_project(self) -> None:
        self.controller.set_project(CorpusProject(), None)

    def _open_project(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Open project", "", "Project (*.json)")
        if not path:
            return
        try:
            project = CorpusProject.load(path)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "Open project", f"Could not open project:\n{exc}")
            return
        self.controller.set_project(project, Path(path))
        missing = project.missing_files()
        if missing:
            listing = "\n".join(str(p) for p in missing[:20])
            QMessageBox.warning(
                self,
                "Missing files",
                f"{len(missing)} referenced file(s) no longer exist:\n{listing}",
            )

    def _save_project(self) -> None:
        if self.controller.project_path is None:
            self._save_project_as()
            return
        self._write(self.controller.project_path)

    def _save_project_as(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "Save project", "project.json", "Project (*.json)"
        )
        if not path:
            return
        self.controller.project_path = Path(path)
        self._write(Path(path))
        self._update_title()

    def _write(self, path: Path) -> None:
        try:
            self.controller.project.save(path)
            self.statusBar().showMessage(f"Saved {path}", 4000)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "Save project", f"Could not save project:\n{exc}")
