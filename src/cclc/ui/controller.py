"""Shared application state wrapping the :class:`CorpusProject` for the views.

The controller is the single source of truth; views connect to :attr:`changed`
to refresh themselves after any structural edit.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, Signal

from cclc.core.corpus import CorpusProject


class ProjectController(QObject):
    changed = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.project = CorpusProject()
        self.case_sensitive = True
        self.project_path: Path | None = None

    def notify(self) -> None:
        self.changed.emit()

    # Convenience pass-throughs that emit ``changed`` -------------------------

    def add_corpus(self, name: str | None = None):
        corpus = self.project.add_corpus(name)
        self.notify()
        return corpus

    def remove_corpus(self, name: str) -> bool:
        ok = self.project.remove_corpus(name)
        if ok:
            self.notify()
        return ok

    def rename_corpus(self, old: str, new: str) -> bool:
        ok = self.project.rename_corpus(old, new)
        if ok:
            self.notify()
        return ok

    def add_files(self, corpus_name: str, paths: list[Path]) -> int:
        added = 0
        for path in paths:
            if self.project.add_file(corpus_name, Path(path)):
                added += 1
        if added:
            self.notify()
        return added

    def remove_file(self, corpus_name: str, path: Path) -> bool:
        ok = self.project.remove_file(corpus_name, path)
        if ok:
            self.notify()
        return ok

    def move_file(self, src: str, dst: str, path: Path) -> bool:
        ok = self.project.move_file(src, dst, path)
        if ok:
            self.notify()
        return ok

    def set_project(self, project: CorpusProject, path: Path | None) -> None:
        self.project = project
        self.project_path = path
        self.notify()
