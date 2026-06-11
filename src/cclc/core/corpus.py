"""The internal data structure: corpora of ELAN files and the project holding them.

Files are only *referenced* by path - they are never moved or copied on disk.
A document cache parses each ``.eaf`` lazily and re-parses it when the file
changes on disk (keyed by path + mtime).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from .elan_document import ElanDocument


@dataclass
class Corpus:
    """An ordered, duplicate-free collection of file paths."""

    name: str
    files: list[Path] = field(default_factory=list)

    def add(self, path: Path) -> bool:
        """Add ``path``; return ``True`` if it was newly added."""
        path = Path(path)
        if path in self.files:
            return False
        self.files.append(path)
        return True

    def remove(self, path: Path) -> bool:
        path = Path(path)
        if path in self.files:
            self.files.remove(path)
            return True
        return False


class CorpusProject:
    """A set of named corpora plus a parse cache; the app's central model."""

    def __init__(self) -> None:
        self.corpora: list[Corpus] = []
        self._cache: dict[Path, tuple[float, ElanDocument]] = {}

    # --- corpus management -----------------------------------------------------

    def corpus_names(self) -> list[str]:
        return [c.name for c in self.corpora]

    def get_corpus(self, name: str) -> Corpus | None:
        for c in self.corpora:
            if c.name == name:
                return c
        return None

    def add_corpus(self, name: str | None = None) -> Corpus:
        if name is None:
            name = self._unique_name("Corpus 1")
        elif self.get_corpus(name) is not None:
            name = self._unique_name(name)
        corpus = Corpus(name=name)
        self.corpora.append(corpus)
        return corpus

    def _unique_name(self, base: str) -> str:
        existing = set(self.corpus_names())
        if base not in existing:
            return base
        # Strip a trailing number from the base, then count up.
        stem = base.rstrip("0123456789").rstrip() or "Corpus"
        i = 1
        while f"{stem} {i}" in existing:
            i += 1
        return f"{stem} {i}"

    def rename_corpus(self, old: str, new: str) -> bool:
        if self.get_corpus(new) is not None and old != new:
            return False
        corpus = self.get_corpus(old)
        if corpus is None:
            return False
        corpus.name = new
        return True

    def remove_corpus(self, name: str) -> bool:
        corpus = self.get_corpus(name)
        if corpus is None:
            return False
        self.corpora.remove(corpus)
        return True

    # --- file operations -------------------------------------------------------

    def add_file(self, corpus_name: str, path: Path) -> bool:
        corpus = self.get_corpus(corpus_name)
        if corpus is None:
            return False
        return corpus.add(Path(path))

    def remove_file(self, corpus_name: str, path: Path) -> bool:
        corpus = self.get_corpus(corpus_name)
        if corpus is None:
            return False
        return corpus.remove(Path(path))

    def move_file(self, src_corpus: str, dst_corpus: str, path: Path) -> bool:
        """Move ``path`` from one corpus to another (move semantics).

        A no-op move onto the same corpus is reported as success.  Moving a file
        the destination already contains still removes it from the source.
        """
        src = self.get_corpus(src_corpus)
        dst = self.get_corpus(dst_corpus)
        if src is None or dst is None:
            return False
        path = Path(path)
        if path not in src.files:
            return False
        if src is dst:
            return True
        dst.add(path)
        src.remove(path)
        return True

    # --- document cache --------------------------------------------------------

    def document(self, path: Path) -> ElanDocument:
        """Return the parsed document for ``path``, using the mtime-keyed cache."""
        path = Path(path)
        mtime = path.stat().st_mtime
        cached = self._cache.get(path)
        if cached is not None and cached[0] == mtime:
            return cached[1]
        doc = ElanDocument.parse(path)
        self._cache[path] = (mtime, doc)
        return doc

    # --- persistence -----------------------------------------------------------

    def to_dict(self) -> dict:
        return {
            "version": 1,
            "corpora": [
                {"name": c.name, "files": [str(p) for p in c.files]} for c in self.corpora
            ],
        }

    @classmethod
    def from_dict(cls, data: dict) -> CorpusProject:
        project = cls()
        for entry in data.get("corpora", []):
            corpus = Corpus(
                name=entry["name"],
                files=[Path(p) for p in entry.get("files", [])],
            )
            project.corpora.append(corpus)
        return project

    def save(self, path: str | Path) -> None:
        Path(path).write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: str | Path) -> CorpusProject:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls.from_dict(data)

    def missing_files(self) -> list[Path]:
        """Referenced paths that no longer exist on disk."""
        missing: list[Path] = []
        for corpus in self.corpora:
            for path in corpus.files:
                if not path.exists():
                    missing.append(path)
        return missing
