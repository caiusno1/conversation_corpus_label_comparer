"""Pure analysis functions: annotation counts and dictionary coverage.

All functions operate on a :class:`~cclc.core.corpus.CorpusProject` and a
:class:`~cclc.core.corpus.Corpus`.  They contain no UI code and are covered by
unit tests against hand-computed values.

Policy decisions (see PLAN.md section 11):

* A tier that is missing in *any* file of the corpus is a hard error
  (:class:`MissingTierError`); analysis runs only when every file has the tier.
  A tier that exists but lacks the searched label simply contributes ``0``.
* Annotation values on a dictionary tier that are not part of the dictionary are
  reported separately and excluded from counts and coverage.
* Label matching is case-sensitive by default; pass ``case_sensitive=False`` to
  fold case.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .corpus import Corpus, CorpusProject


class MissingTierError(Exception):
    """Raised when one or more corpus files do not contain the requested tier."""

    def __init__(self, tier_name: str, files: list[Path]):
        self.tier_name = tier_name
        self.files = files
        listing = ", ".join(p.name for p in files)
        super().__init__(
            f"Tier {tier_name!r} is missing in {len(files)} file(s): {listing}. "
            "Analysis requires the tier to be present in every file of the corpus."
        )


def _norm(value: str, case_sensitive: bool) -> str:
    return value if case_sensitive else value.casefold()


def label_matches(value: str, label: str, case_sensitive: bool = True) -> bool:
    return _norm(value, case_sensitive) == _norm(label, case_sensitive)


def require_tier_everywhere(
    project: CorpusProject, corpus: Corpus, tier_name: str
) -> None:
    """Raise :class:`MissingTierError` unless every file contains ``tier_name``."""
    missing = [p for p in corpus.files if tier_name not in project.document(p).tiers]
    if missing:
        raise MissingTierError(tier_name, missing)


def union_dictionary(
    project: CorpusProject, corpus: Corpus, tier_name: str
) -> list[str]:
    """Union of the controlled-vocabulary labels for ``tier_name`` across files.

    Order follows first appearance; files may carry slightly different CV
    versions, and the union keeps every label selectable.
    """
    seen: set[str] = set()
    ordered: list[str] = []
    for path in corpus.files:
        for label in project.document(path).dictionary_for_tier(tier_name):
            if label not in seen:
                seen.add(label)
                ordered.append(label)
    return ordered


def dictionary_tiers(project: CorpusProject, corpus: Corpus) -> list[str]:
    """Tier names that follow a dictionary in at least one file, sorted."""
    names: set[str] = set()
    for path in corpus.files:
        names.update(project.document(path).dictionary_tier_names())
    return sorted(names)


@dataclass
class CountResult:
    """Per-file counts of one label on one tier plus descriptive statistics."""

    tier_name: str
    label: str
    per_file: dict[Path, int] = field(default_factory=dict)
    out_of_dictionary: dict[Path, int] = field(default_factory=dict)

    @property
    def total(self) -> int:
        return sum(self.per_file.values())

    @property
    def mean(self) -> float:
        if not self.per_file:
            return 0.0
        return self.total / len(self.per_file)

    @property
    def stdev(self) -> float | None:
        """Sample standard deviation (n-1); ``None`` for fewer than two files."""
        values = list(self.per_file.values())
        if len(values) < 2:
            return None
        return statistics.stdev(values)


def count_label(
    project: CorpusProject,
    corpus: Corpus,
    tier_name: str,
    label: str,
    case_sensitive: bool = True,
) -> CountResult:
    """Count ``label`` on ``tier_name`` per file across the corpus.

    Raises :class:`MissingTierError` if any file lacks the tier.  Out-of-dictionary
    annotation values are tallied separately in ``out_of_dictionary``.
    """
    require_tier_everywhere(project, corpus, tier_name)
    result = CountResult(tier_name=tier_name, label=label)
    dictionary = set(union_dictionary(project, corpus, tier_name))

    for path in corpus.files:
        tier = project.document(path).tiers[tier_name]
        count = 0
        ood = 0
        for ann in tier.annotations:
            if not ann.value:
                continue
            if label_matches(ann.value, label, case_sensitive):
                count += 1
            elif ann.value not in dictionary:
                ood += 1
        result.per_file[path] = count
        result.out_of_dictionary[path] = ood
    return result


@dataclass
class CoverageResult:
    """Dictionary coverage of one tier per file plus the mean across files."""

    tier_name: str
    dictionary_size: int
    per_file_covered: dict[Path, int] = field(default_factory=dict)

    @property
    def per_file_percent(self) -> dict[Path, float]:
        if self.dictionary_size == 0:
            return {p: 0.0 for p in self.per_file_covered}
        return {
            p: 100.0 * covered / self.dictionary_size
            for p, covered in self.per_file_covered.items()
        }

    @property
    def mean_coverage(self) -> float:
        percents = list(self.per_file_percent.values())
        if not percents:
            return 0.0
        return statistics.fmean(percents)


def coverage(
    project: CorpusProject,
    corpus: Corpus,
    tier_name: str,
    case_sensitive: bool = True,
) -> CoverageResult:
    """Compute dictionary coverage of ``tier_name`` for every file.

    coverage(file) = |dictionary labels present at least once in the file| /
    |dictionary| * 100 %.  The dictionary is the union across the corpus.
    Raises :class:`MissingTierError` if any file lacks the tier.
    """
    require_tier_everywhere(project, corpus, tier_name)
    dictionary = union_dictionary(project, corpus, tier_name)
    norm_dictionary = {_norm(label, case_sensitive): label for label in dictionary}
    result = CoverageResult(tier_name=tier_name, dictionary_size=len(dictionary))

    for path in corpus.files:
        tier = project.document(path).tiers[tier_name]
        present: set[str] = set()
        for ann in tier.annotations:
            if not ann.value:
                continue
            key = _norm(ann.value, case_sensitive)
            if key in norm_dictionary:
                present.add(key)
        result.per_file_covered[path] = len(present)
    return result


@dataclass
class IntervalResult:
    """Counts of every dictionary label within a time interval, per file.

    Coverage mirrors :class:`CoverageResult` but is restricted to the interval:
    a label counts as covered in a file when at least one of its annotations
    lies within the interval.
    """

    tier_name: str
    start_ms: int
    end_ms: int
    dictionary: list[str]
    per_file_label_counts: dict[Path, dict[str, int]] = field(default_factory=dict)
    out_of_dictionary: dict[Path, int] = field(default_factory=dict)

    @property
    def dictionary_size(self) -> int:
        return len(self.dictionary)

    @property
    def per_file_covered(self) -> dict[Path, int]:
        return {
            p: sum(1 for v in counts.values() if v > 0)
            for p, counts in self.per_file_label_counts.items()
        }

    @property
    def per_file_percent(self) -> dict[Path, float]:
        if self.dictionary_size == 0:
            return {p: 0.0 for p in self.per_file_label_counts}
        return {
            p: 100.0 * covered / self.dictionary_size
            for p, covered in self.per_file_covered.items()
        }

    @property
    def mean_coverage(self) -> float:
        percents = list(self.per_file_percent.values())
        if not percents:
            return 0.0
        return statistics.fmean(percents)


def interval_label_counts(
    project: CorpusProject,
    corpus: Corpus,
    tier_name: str,
    start_ms: int,
    end_ms: int,
    mode: str = "contained",
    case_sensitive: bool = True,
) -> IntervalResult:
    """Count every dictionary label on ``tier_name`` within ``[start_ms, end_ms]``.

    ``mode`` selects what "within" means: ``"contained"`` requires the whole
    annotation to lie inside the bounds (boundary-inclusive); ``"overlapping"``
    requires the annotation to intersect the interval (merely touching an edge
    does not count).  Untimed annotations are skipped.  Raises
    :class:`MissingTierError` if any file lacks the tier.
    """
    if mode not in ("contained", "overlapping"):
        raise ValueError(f"unknown interval mode {mode!r}")
    require_tier_everywhere(project, corpus, tier_name)
    dictionary = union_dictionary(project, corpus, tier_name)
    norm_to_canonical = {_norm(label, case_sensitive): label for label in dictionary}
    result = IntervalResult(
        tier_name=tier_name, start_ms=start_ms, end_ms=end_ms, dictionary=dictionary
    )

    for path in corpus.files:
        tier = project.document(path).tiers[tier_name]
        counts = {label: 0 for label in dictionary}
        ood = 0
        for ann in tier.annotations:
            if ann.start_ms is None or ann.end_ms is None or not ann.value:
                continue
            if mode == "contained":
                inside = ann.start_ms >= start_ms and ann.end_ms <= end_ms
            else:
                inside = ann.start_ms < end_ms and ann.end_ms > start_ms
            if not inside:
                continue
            canonical = norm_to_canonical.get(_norm(ann.value, case_sensitive))
            if canonical is None:
                ood += 1
            else:
                counts[canonical] += 1
        result.per_file_label_counts[path] = counts
        result.out_of_dictionary[path] = ood
    return result


def corpus_time_extent(project: CorpusProject, corpus: Corpus) -> int:
    """Largest annotation end time (ms) across all tiers and files; 0 if none.

    Unreadable files are skipped - this feeds UI slider ranges and must not
    fail because of one broken file.
    """
    extent = 0
    for path in corpus.files:
        try:
            doc = project.document(path)
        except Exception:  # noqa: BLE001
            continue
        for tier in doc.tiers.values():
            for ann in tier.annotations:
                if ann.end_ms is not None and ann.end_ms > extent:
                    extent = ann.end_ms
    return extent
