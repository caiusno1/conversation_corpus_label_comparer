"""Transition matrices over dictionary tiers, in three modes (PLAN.md section 9).

All modes produce a **row-stochastic** matrix: cell *(i, j)* = how often element
*i* is immediately followed by element *j*, divided by the number of transitions
*out of* *i* (i.e. the occurrences of *i* that have a successor).  Each row
therefore sums to 1; a row is empty (every cell ``None``) when its element never
has a successor - e.g. a label that only ever ends a sequence.

* :func:`transition_matrix` - **merged sequence**: the annotations of the
  selected tiers are merged into one sequence ordered by start time; elements
  are the labels of the union dictionary (cross-tier transitions possible).
* :func:`tier_to_tier_matrix` - **tier to tier**: rows are the *source* tier
  labels, columns the *target* tier labels; for every source annotation its
  next target annotation (first one starting strictly later) is the successor.
* :func:`compound_transition_matrix` - **compound to compound**: elements are
  instances of named compounds found by the query engine (AND / OR / NOT plus
  max distance, see :mod:`cclc.core.query`); instances are ordered by start time.

Out-of-dictionary values, empty values and untimed annotations are skipped
transparently.  Sequences never cross file boundaries; corpus scope sums the
per-file transition counts before normalising.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from .analysis import _norm, require_tier_everywhere, union_dictionary
from .query import Query, evaluate, free_term_keys

if TYPE_CHECKING:
    from .corpus import Corpus, CorpusProject
    from .elan_document import ElanDocument


@dataclass
class TransitionResult:
    """Counts and per-row totals backing one transition matrix.

    ``row_labels`` are the "from" elements *i*, ``col_labels`` the "to" elements
    *j*; ``counts[(i, j)]`` is the number of ``i -> j`` transitions and
    ``row_totals[i]`` the number of transitions out of *i* (the row denominator).
    For the merged-sequence and compound modes the row and column labels are the
    same set.
    """

    row_labels: list[str]
    col_labels: list[str]
    counts: dict[tuple[str, str], int] = field(default_factory=dict)  # (i, j) = i->j
    row_totals: dict[str, int] = field(default_factory=dict)  # transitions out of i
    annotations_considered: int = 0

    def record(self, from_label: str, to_label: str) -> None:
        self.counts[(from_label, to_label)] = self.counts.get((from_label, to_label), 0) + 1
        self.row_totals[from_label] = self.row_totals.get(from_label, 0) + 1

    def count(self, from_label: str, to_label: str) -> int:
        """Raw count of ``from_label -> to_label`` transitions."""
        return self.counts.get((from_label, to_label), 0)

    def ratio(self, from_label: str, to_label: str) -> float | None:
        """Probability of ``to_label`` immediately following ``from_label``.

        ``count(i, j)`` divided by the transitions out of *i*; ``None`` when *i*
        never has a successor (the whole row is then undefined).
        """
        total = self.row_totals.get(from_label, 0)
        if total == 0:
            return None
        return self.count(from_label, to_label) / total

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


def _record_sequence(result: TransitionResult, sequence: list[str]) -> None:
    """Count consecutive transitions in one file's element sequence."""
    result.annotations_considered += len(sequence)
    for previous, current in zip(sequence, sequence[1:], strict=False):
        result.record(previous, current)


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
        result.row_totals[label] = 0

    for path in _scope_files(corpus, scope_file):
        doc = project.document(path)
        merged: list[tuple[int, int, str]] = []
        for tier in tier_names:
            merged.extend(_sequence(doc, tier, norm_to_canonical, case_sensitive))
        merged.sort(key=lambda item: (item[0], item[1]))
        _record_sequence(result, [label for _, _, label in merged])
    return result


def tier_to_tier_matrix(
    project: CorpusProject,
    corpus: Corpus,
    source_tier: str,
    target_tier: str,
    scope_file: Path | None = None,
    case_sensitive: bool = True,
) -> TransitionResult:
    """Tier-to-tier mode: from each source annotation to its next target one.

    Rows are the ``source_tier`` labels (the "from"), columns the ``target_tier``
    labels (the "to").  For every annotation with label *i* on the source tier,
    the first annotation on the target tier whose start time is **strictly
    later** is its successor *j*.  Each row sums to 1; a source annotation with
    no later target on the target tier contributes no transition.
    """
    for tier in dict.fromkeys((source_tier, target_tier)):
        require_tier_everywhere(project, corpus, tier)
    row_labels = union_dictionary(project, corpus, source_tier)
    col_labels = union_dictionary(project, corpus, target_tier)
    source_norm = {_norm(label, case_sensitive): label for label in row_labels}
    target_norm = {_norm(label, case_sensitive): label for label in col_labels}

    result = TransitionResult(row_labels=row_labels, col_labels=col_labels)
    for label in row_labels:
        result.row_totals[label] = 0

    for path in _scope_files(corpus, scope_file):
        doc = project.document(path)
        sources = _sequence(doc, source_tier, source_norm, case_sensitive)
        targets = _sequence(doc, target_tier, target_norm, case_sensitive)
        result.annotations_considered += len(sources) + len(targets)

        # Both lists are start-ordered: advance a single pointer per source.
        index = 0
        for s_start, _, s_label in sources:
            while index < len(targets) and targets[index][0] <= s_start:
                index += 1
            if index < len(targets):
                result.record(s_label, targets[index][2])
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
    how often an instance of compound *i* is immediately followed by an instance
    of compound *j*, divided by the transitions out of *i*.

    A compound containing **free (ALL) terms** is expanded by its bindings:
    every instance becomes an element named ``name[label, ...]`` with the labels
    the free terms matched, so the matrix differentiates the bound values.
    """
    if not queries:
        raise ValueError("define at least one compound")

    elements: dict[str, None] = {}  # ordered set of matrix elements
    events_per_file: dict[Path, list[tuple[int, int, str]]] = {}
    for name, query in queries.items():
        free_keys = free_term_keys(query.root)
        seen_elements: set[str] = set()
        for instance in evaluate(project, corpus, query):
            if scope_file is not None and instance.file != scope_file:
                continue
            element = name
            if free_keys:
                bound = [
                    instance.matched[key].value
                    for key in free_keys
                    if key in instance.matched
                ]
                element = f"{name}[{', '.join(bound)}]"
            seen_elements.add(element)
            events_per_file.setdefault(instance.file, []).append(
                (instance.start_ms, instance.end_ms, element)
            )
        if free_keys:
            for element in sorted(seen_elements):
                elements[element] = None
        else:
            elements[name] = None  # plain compounds always appear, even with n=0

    names = list(elements)
    result = TransitionResult(row_labels=names, col_labels=names)
    for name in names:
        result.row_totals[name] = 0

    for events in events_per_file.values():
        events.sort(key=lambda item: (item[0], item[1], item[2]))
        _record_sequence(result, [name for _, _, name in events])
    return result
