"""Transition matrices over the label sequences of dictionary tiers.

Cell *(i, j)* holds how often label *i* appeared immediately after label *j*,
divided by the number of all instances of label *i* (see PLAN.md section 9).
The annotations of all selected tiers are merged into one sequence ordered by
start time, which makes cross-tier transitions possible; the label set is the
union of the selected tiers' dictionaries.  Out-of-dictionary values, empty
values and untimed annotations are skipped transparently (the surrounding
annotations become adjacent).  Transitions never cross file boundaries; the
corpus-level matrix sums the per-file counts before normalising.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from .analysis import _norm, require_tier_everywhere, union_dictionary

if TYPE_CHECKING:
    from .corpus import Corpus, CorpusProject


@dataclass
class TransitionResult:
    """Counts and totals backing one transition matrix."""

    labels: list[str]
    counts: dict[tuple[str, str], int] = field(default_factory=dict)  # (i, j)
    label_totals: dict[str, int] = field(default_factory=dict)
    annotations_considered: int = 0

    def count(self, label_i: str, label_j: str) -> int:
        """Raw count: how often ``label_i`` appeared right after ``label_j``."""
        return self.counts.get((label_i, label_j), 0)

    def ratio(self, label_i: str, label_j: str) -> float | None:
        """``count(i, j)`` divided by all instances of ``label_i``.

        ``None`` when ``label_i`` never occurs (the ratio is undefined).
        """
        total = self.label_totals.get(label_i, 0)
        if total == 0:
            return None
        return self.count(label_i, label_j) / total

    @property
    def transition_total(self) -> int:
        return sum(self.counts.values())


def transition_matrix(
    project: CorpusProject,
    corpus: Corpus,
    tier_names: list[str],
    scope_file: Path | None = None,
    case_sensitive: bool = True,
) -> TransitionResult:
    """Compute the transition matrix for the merged sequence of ``tier_names``.

    ``scope_file`` restricts the computation to one file of the corpus;
    ``None`` aggregates the whole corpus (counts are summed per file, then
    normalised - sequences never continue across a file boundary).  Every
    selected tier must exist in every file of the corpus
    (:class:`~cclc.core.analysis.MissingTierError` otherwise).
    """
    if not tier_names:
        raise ValueError("select at least one tier")
    for tier in tier_names:
        require_tier_everywhere(project, corpus, tier)

    # Union of the selected tiers' dictionaries, in selection order.
    labels: list[str] = []
    seen: set[str] = set()
    for tier in tier_names:
        for label in union_dictionary(project, corpus, tier):
            if label not in seen:
                seen.add(label)
                labels.append(label)
    norm_to_canonical = {_norm(label, case_sensitive): label for label in labels}

    result = TransitionResult(labels=labels)
    for label in labels:
        result.label_totals[label] = 0

    files = [scope_file] if scope_file is not None else list(corpus.files)
    for path in files:
        doc = project.document(path)
        sequence: list[tuple[int, int, str]] = []
        for tier in tier_names:
            tier_obj = doc.tiers.get(tier)
            if tier_obj is None:  # cannot happen after require_tier_everywhere
                continue
            for ann in tier_obj.annotations:
                if ann.start_ms is None or not ann.value:
                    continue
                canonical = norm_to_canonical.get(_norm(ann.value, case_sensitive))
                if canonical is None:
                    continue  # out-of-dictionary values are skipped
                end = ann.end_ms if ann.end_ms is not None else ann.start_ms
                sequence.append((ann.start_ms, end, canonical))
        sequence.sort(key=lambda item: (item[0], item[1]))

        previous: str | None = None
        for _, _, label in sequence:
            result.label_totals[label] += 1
            result.annotations_considered += 1
            if previous is not None:
                key = (label, previous)
                result.counts[key] = result.counts.get(key, 0) + 1
            previous = label
    return result
