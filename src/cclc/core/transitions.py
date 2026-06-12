"""Transition matrices over dictionary tiers, in three modes (PLAN.md section 9).

All modes share the cell definition: cell *(i, j)* = how often element *i*
appeared immediately after element *j*, divided by the number of all instances
of element *i* (so the row header carries the denominator).

* :func:`transition_matrix` - **merged sequence**: the annotations of the
  selected tiers are merged into one sequence ordered by start time; elements
  are the labels of the union dictionary (cross-tier transitions possible).
* :func:`tier_to_tier_matrix` - **tier to tier**: for every annotation on the
  source tier (label *j*), the *next* annotation on the target tier - the first
  one starting strictly later - is looked up (label *i*).  Rows are the target
  dictionary, columns the source dictionary; rows can sum to more than 1
  because several source annotations may share the same next target.
* :func:`compound_transition_matrix` - **compound to compound**: elements are
  instances of named compounds found by the query engine (AND / OR / NOT plus
  max distance, see :mod:`cclc.core.query`); instances are ordered by their
  start time per file.

Out-of-dictionary values, empty values and untimed annotations are skipped
transparently.  Sequences never cross file boundaries; corpus scope sums the
per-file counts before normalising.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from .analysis import _norm, require_tier_everywhere, union_dictionary
from .query import Query, evaluate

if TYPE_CHECKING:
    from .corpus import Corpus, CorpusProject
    from .elan_document import ElanDocument


@dataclass
class TransitionResult:
    """Counts and totals backing one transition matrix.

    ``row_labels`` are the elements *i* (the following element), ``col_labels``
    the elements *j* (the predecessor); for the merged-sequence mode both are
    the same union dictionary.
    """

    row_labels: list[str]
    col_labels: list[str]
    counts: dict[tuple[str, str], int] = field(default_factory=dict)  # (i, j)
    label_totals: dict[str, int] = field(default_factory=dict)  # totals of i
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


def _sequence(
    doc: ElanDocument,
    tier_name: str,
    norm_to_canonical: dict[str, str],
    case_sensitive: bool,
) -> list[tuple[int, int, str]]:
    """Time-ordered ``(start, end, canonical label)`` triples of one tier.

    Untimed, empty and out-of-dictionary annotations are skipped.
    """
    tier = doc.tiers.get(tier_name)
    if tier is None:  # callers validate via require_tier_everywhere
        return []
    out: list[tuple[int, int, str]] = []
    for ann in tier.annotations:
        if ann.start_ms is None or not ann.value:
            continue
        canonical = norm_to_canonical.get(_norm(ann.value, case_sensitive))
        if canonical is None:
            continue
        end = ann.end_ms if ann.end_ms is not None else ann.start_ms
        out.append((ann.start_ms, end, canonical))
    out.sort(key=lambda item: (item[0], item[1]))
    return out


def _scope_files(corpus: Corpus, scope_file: Path | None) -> list[Path]:
    return [scope_file] if scope_file is not None else list(corpus.files)


def transition_matrix(
    project: CorpusProject,
    corpus: Corpus,
    tier_names: list[str],
    scope_file: Path | None = None,
    case_sensitive: bool = True,
) -> TransitionResult:
    """Merged-sequence mode: transitions within the union sequence of the tiers.

    ``scope_file`` restricts the computation to one file of the corpus;
    ``None`` aggregates the whole corpus.  Every selected tier must exist in
    every file of the corpus (:class:`~cclc.core.analysis.MissingTierError`).
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

    result = TransitionResult(row_labels=labels, col_labels=labels)
    for label in labels:
        result.label_totals[label] = 0

    for path in _scope_files(corpus, scope_file):
        doc = project.document(path)
        merged: list[tuple[int, int, str]] = []
        for tier in tier_names:
            merged.extend(_sequence(doc, tier, norm_to_canonical, case_sensitive))
        merged.sort(key=lambda item: (item[0], item[1]))

        previous: str | None = None
        for _, _, label in merged:
            result.label_totals[label] += 1
            result.annotations_considered += 1
            if previous is not None:
                key = (label, previous)
                result.counts[key] = result.counts.get(key, 0) + 1
            previous = label
    return result


def tier_to_tier_matrix(
    project: CorpusProject,
    corpus: Corpus,
    source_tier: str,
    target_tier: str,
    scope_file: Path | None = None,
    case_sensitive: bool = True,
) -> TransitionResult:
    """Tier-to-tier mode: from each source annotation to the next target one.

    For every annotation with label *j* on ``source_tier``, the first
    annotation on ``target_tier`` whose start time is **strictly later** is the
    transition target (label *i*).  Cell (i, j) is normalised by all instances
    of label *i* on the target tier; rows can therefore sum to more than 1 when
    several source annotations share the same next target annotation.
    """
    for tier in dict.fromkeys((source_tier, target_tier)):
        require_tier_everywhere(project, corpus, tier)
    col_labels = union_dictionary(project, corpus, source_tier)
    row_labels = union_dictionary(project, corpus, target_tier)
    col_norm = {_norm(label, case_sensitive): label for label in col_labels}
    row_norm = {_norm(label, case_sensitive): label for label in row_labels}

    result = TransitionResult(row_labels=row_labels, col_labels=col_labels)
    for label in row_labels:
        result.label_totals[label] = 0

    for path in _scope_files(corpus, scope_file):
        doc = project.document(path)
        sources = _sequence(doc, source_tier, col_norm, case_sensitive)
        targets = _sequence(doc, target_tier, row_norm, case_sensitive)

        for _, _, label in targets:
            result.label_totals[label] += 1
            result.annotations_considered += 1
        result.annotations_considered += len(sources)

        # Both lists are start-ordered: advance a single pointer per source.
        index = 0
        for s_start, _, s_label in sources:
            while index < len(targets) and targets[index][0] <= s_start:
                index += 1
            if index < len(targets):
                key = (targets[index][2], s_label)
                result.counts[key] = result.counts.get(key, 0) + 1
    return result


def compound_transition_matrix(
    project: CorpusProject,
    corpus: Corpus,
    queries: dict[str, Query],
    scope_file: Path | None = None,
) -> TransitionResult:
    """Compound mode: transitions between instances of named compounds.

    ``queries`` maps a compound name to a query expression; instances are found
    with the query engine, anchored at their start time, merged per file into
    one sequence (ties ordered by end time, then name) and cell (i, j) counts
    how often an instance of compound *i* immediately follows an instance of
    compound *j*, divided by all instances of *i*.
    """
    if not queries:
        raise ValueError("define at least one compound")
    names = list(queries)
    result = TransitionResult(row_labels=names, col_labels=names)
    for name in names:
        result.label_totals[name] = 0

    events_per_file: dict[Path, list[tuple[int, int, str]]] = {}
    for name, query in queries.items():
        for instance in evaluate(project, corpus, query):
            if scope_file is not None and instance.file != scope_file:
                continue
            events_per_file.setdefault(instance.file, []).append(
                (instance.start_ms, instance.end_ms, name)
            )

    for events in events_per_file.values():
        events.sort(key=lambda item: (item[0], item[1], item[2]))
        previous: str | None = None
        for _, _, name in events:
            result.label_totals[name] += 1
            result.annotations_considered += 1
            if previous is not None:
                key = (name, previous)
                result.counts[key] = result.counts.get(key, 0) + 1
            previous = name
    return result
