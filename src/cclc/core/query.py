"""Visual-query model and evaluator over annotation co-occurrence.

A query combines *terms* (``tier = label``) with AND / OR / NOT and a temporal
constraint, and produces *instances* ("compounds"): sets of co-occurring
annotations.  The engine is pure Python and unit-tested.

Semantics (see PLAN.md sections 7.1 and 11):

* **Reference point** - distance between two annotations is the absolute
  difference of a selectable point: ``begin``, ``mid`` or ``end``.
* **Anchor** - the first non-negated term (depth-first) is the anchor.  Each
  anchor annotation yields at most one instance in the default ``"anchor"``
  counting mode; ``"combinations"`` emits every satisfying tuple instead.
* **Positive tuple** - all positive annotations of an instance must be
  **pairwise** within the max distance (or satisfy the active interval relation
  against the anchor).  Candidates are tried nearest-to-the-anchor first, with
  backtracking when a later constraint rejects a choice.
* **NOT (near-any-member rule)** - a negated term/group rejects the instance iff
  a matching annotation lies within the max distance of **at least one** of the
  instance's positive annotations.
* **Interval relations** - an AND-group may instead require an Allen-style
  relation (overlaps / contains / during / meets / starts / finishes) between the
  anchor and the matched annotation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from .analysis import label_matches, require_tier_everywhere
from .elan_document import Annotation, ElanDocument

if TYPE_CHECKING:
    from .corpus import Corpus, CorpusProject

RELATIONS = ("overlaps", "contains", "during", "meets", "starts", "finishes")


# --- query AST ----------------------------------------------------------------


@dataclass
class Term:
    """A single ``tier = label`` condition, optionally negated."""

    tier: str
    label: str
    negated: bool = False

    def key(self) -> str:
        return f"{self.tier}={self.label}"


@dataclass
class Group:
    """An AND ("ALL of") or OR ("ANY of") combination of nodes.

    ``relation`` (AND-groups only) replaces the distance constraint with an
    Allen-style interval relation between the anchor and each matched annotation.
    """

    op: str  # "AND" or "OR"
    children: list[Term | Group] = field(default_factory=list)
    negated: bool = False
    relation: str | None = None

    def __post_init__(self) -> None:
        if self.op not in ("AND", "OR"):
            raise ValueError(f"group op must be AND or OR, got {self.op!r}")
        if self.relation is not None and self.relation not in RELATIONS:
            raise ValueError(f"unknown relation {self.relation!r}")


Node = Term | Group


@dataclass
class Query:
    """A complete query: an expression tree plus the temporal parameters."""

    root: Group
    max_distance_ms: int
    reference_point: str = "begin"  # begin|mid|end
    counting_mode: str = "anchor"  # anchor|combinations

    def positive_term_tiers(self) -> set[str]:
        tiers: set[str] = set()
        _collect_positive_tiers(self.root, negated_ctx=False, out=tiers)
        return tiers

    def validate(self) -> None:
        """Raise ``ValueError`` if the query is structurally unusable."""
        if _first_positive_term(self.root) is None:
            raise ValueError("query must contain at least one non-negated term")
        if self.reference_point not in ("begin", "mid", "end"):
            raise ValueError(f"unknown reference point {self.reference_point!r}")
        if self.counting_mode not in ("anchor", "combinations"):
            raise ValueError(f"unknown counting mode {self.counting_mode!r}")


@dataclass
class Instance:
    """One query match: the annotations forming a compound, in one file."""

    file: Path
    matched: dict[str, Annotation]  # term key -> matched annotation
    start_ms: int
    end_ms: int

    def label_combination(self) -> tuple[str, ...]:
        return tuple(sorted(self.matched.keys()))


# --- AST helpers --------------------------------------------------------------


def _collect_positive_tiers(node: Node, negated_ctx: bool, out: set[str]) -> None:
    if isinstance(node, Term):
        if not (node.negated or negated_ctx):
            out.add(node.tier)
        return
    child_ctx = negated_ctx != node.negated  # XOR: double negation cancels
    for child in node.children:
        _collect_positive_tiers(child, child_ctx, out)


def _first_positive_term(node: Node) -> Term | None:
    """The first non-negated term in depth-first order (the anchor term)."""
    if isinstance(node, Term):
        return None if node.negated else node
    if node.negated:
        return None
    for child in node.children:
        found = _first_positive_term(child)
        if found is not None:
            return found
    return None


# --- distance / relations -----------------------------------------------------


def _distance(a: Annotation, b: Annotation, mode: str) -> int | None:
    pa = a.reference_point(mode)
    pb = b.reference_point(mode)
    if pa is None or pb is None:
        return None
    return abs(pa - pb)


def _relation_holds(relation: str, anchor: Annotation, other: Annotation) -> bool:
    if None in (anchor.start_ms, anchor.end_ms, other.start_ms, other.end_ms):
        return False
    a0, a1 = anchor.start_ms, anchor.end_ms
    b0, b1 = other.start_ms, other.end_ms
    if relation == "overlaps":
        return a0 < b1 and b0 < a1
    if relation == "contains":
        return a0 <= b0 and b1 <= a1
    if relation == "during":
        return b0 <= a0 and a1 <= b1
    if relation == "meets":
        return a1 == b0 or b1 == a0
    if relation == "starts":
        return a0 == b0
    if relation == "finishes":
        return a1 == b1
    return False


def _pair_ok(a: Annotation, b: Annotation, query: Query) -> bool:
    dist = _distance(a, b, query.reference_point)
    return dist is not None and dist <= query.max_distance_ms


def _near_any(
    ann: Annotation, positives: list[Annotation], query: Query, relation: str | None
) -> bool:
    """Whether ``ann`` is "near" the tuple: within the max distance of at least
    one positive annotation, or (in relation mode) related to the anchor."""
    if relation is not None:
        return bool(positives) and _relation_holds(relation, positives[0], ann)
    return any(_pair_ok(ann, p, query) for p in positives)


# --- evaluation ---------------------------------------------------------------


def evaluate(project: CorpusProject, corpus: Corpus, query: Query) -> list[Instance]:
    """Evaluate ``query`` over every file of ``corpus``.

    Positive-term tiers must exist in every file (else
    :class:`~cclc.core.analysis.MissingTierError`).  Negated-term tiers may be
    absent - a missing tier simply yields no matches to exclude.
    """
    query.validate()
    for tier in query.positive_term_tiers():
        require_tier_everywhere(project, corpus, tier)

    instances: list[Instance] = []
    for path in corpus.files:
        doc = project.document(path)
        instances.extend(_evaluate_file(doc, path, query))
    return instances


def _evaluate_file(doc: ElanDocument, path: Path, query: Query) -> list[Instance]:
    # A top-level OR is split so each branch anchors on its own first term.
    if query.root.op == "OR" and not query.root.negated:
        out: list[Instance] = []
        for child in query.root.children:
            sub = Query(
                root=child if isinstance(child, Group) else Group("AND", [child]),
                max_distance_ms=query.max_distance_ms,
                reference_point=query.reference_point,
                counting_mode=query.counting_mode,
            )
            out.extend(_evaluate_file(doc, path, sub))
        return _dedupe(out)

    anchor_term = _first_positive_term(query.root)
    if anchor_term is None:
        return []
    anchor_tier = doc.tiers.get(anchor_term.tier)
    if anchor_tier is None:
        return []

    anchors = [
        a
        for a in anchor_tier.annotations
        if a.value and label_matches(a.value, anchor_term.label, case_sensitive=True)
    ]

    out: list[Instance] = []
    for anchor in anchors:
        seed = {anchor_term.key(): anchor}
        for state, pending in _assignments(query.root, seed, (), doc, query, None):
            positives = list(state.values())
            if any(_blocks(node, rel, positives, doc, query) for node, rel in pending):
                continue
            out.append(_make_instance(path, state, query))
            if query.counting_mode == "anchor":
                break
    return _dedupe(out)


def _assignments(
    node: Node,
    state: dict[str, Annotation],
    pending: tuple,
    doc: ElanDocument,
    query: Query,
    relation: str | None,
):
    """Yield ``(state, pending)`` pairs satisfying the positive part of ``node``.

    ``state`` maps term keys to the chosen annotations (the anchor is
    pre-seeded); every new choice must be compatible with ALL annotations chosen
    so far - pairwise max distance, or the active interval relation against the
    anchor.  Negated nodes are deferred into ``pending`` (with their relation
    context) and checked against the completed tuple afterwards, which also
    gives backtracking: if the nearest candidate tuple is rejected by a NOT,
    the next one is tried.
    """
    if isinstance(node, Term):
        if node.negated:
            yield state, (*pending, (node, relation))
            return
        if node.key() in state:
            yield state, pending
            return
        for ann in _compatible_candidates(node, list(state.values()), doc, query, relation):
            yield {**state, node.key(): ann}, pending
        return

    if node.negated:
        yield state, (*pending, (node, relation))
        return

    active = node.relation if node.relation is not None else relation
    if node.op == "AND":
        states = [(state, pending)]
        for child in node.children:
            states = [
                extended
                for current in states
                for extended in _assignments(child, current[0], current[1], doc, query, active)
            ]
            if not states:
                return
        yield from states
    else:  # OR: each child is a separate way to satisfy the group
        for child in node.children:
            yield from _assignments(child, state, pending, doc, query, active)


def _label_annotations(term: Term, doc: ElanDocument) -> list[Annotation]:
    """All annotations on ``term``'s tier whose value matches its label."""
    tier = doc.tiers.get(term.tier)
    if tier is None:
        return []
    return [
        ann
        for ann in tier.annotations
        if ann.value and label_matches(ann.value, term.label, case_sensitive=True)
    ]


def _compatible_candidates(
    term: Term,
    positives: list[Annotation],
    doc: ElanDocument,
    query: Query,
    relation: str | None,
) -> list[Annotation]:
    """Annotations matching ``term`` that fit the tuple built so far.

    Distance mode: pairwise within the max distance of every chosen annotation.
    Relation mode: the Allen relation must hold against the anchor (the first
    chosen annotation).  Sorted nearest-to-the-anchor first so the default
    anchor counting picks the closest satisfying tuple.
    """
    anchor = positives[0] if positives else None
    out: list[tuple[int, Annotation]] = []
    for ann in _label_annotations(term, doc):
        if relation is not None:
            if anchor is None or not _relation_holds(relation, anchor, ann):
                continue
        elif not all(_pair_ok(ann, p, query) for p in positives):
            continue
        dist = (
            _distance(ann, anchor, query.reference_point) if anchor is not None else 0
        )
        out.append((dist if dist is not None else 1 << 30, ann))
    out.sort(key=lambda t: t[0])
    return [ann for _, ann in out]


def _blocks(
    node: Node,
    relation: str | None,
    positives: list[Annotation],
    doc: ElanDocument,
    query: Query,
) -> bool:
    """Whether a deferred negated node finds a match near the finished tuple.

    A negated **term** blocks iff one of its annotations lies within the max
    distance of at least one positive annotation (near-any-member rule); in
    relation mode the relation is tested against the anchor instead.  A negated
    **group** blocks iff its children are satisfiable that way under the group's
    operator; negations nested inside invert their child's result.
    """
    if isinstance(node, Term):
        return any(
            _near_any(ann, positives, query, relation)
            for ann in _label_annotations(node, doc)
        )
    active = node.relation if node.relation is not None else relation
    results = []
    for child in node.children:
        result = _blocks(child, active, positives, doc, query)
        if child.negated:
            result = not result
        results.append(result)
    if not results:
        return False
    return all(results) if node.op == "AND" else any(results)


def _make_instance(path: Path, matched: dict[str, Annotation], query: Query) -> Instance:
    starts = [a.start_ms for a in matched.values() if a.start_ms is not None]
    ends = [a.end_ms for a in matched.values() if a.end_ms is not None]
    start = min(starts) if starts else 0
    end = max(ends) if ends else 0
    return Instance(file=path, matched=dict(matched), start_ms=start, end_ms=end)


def _dedupe(instances: list[Instance]) -> list[Instance]:
    """Remove instances with an identical set of matched annotations."""
    seen: set[tuple] = set()
    out: list[Instance] = []
    for inst in instances:
        sig = tuple(
            sorted(
                (k, a.value, a.start_ms, a.end_ms) for k, a in inst.matched.items()
            )
        )
        if sig not in seen:
            seen.add(sig)
            out.append(inst)
    return out


# --- statistics over a selection of instances ---------------------------------


@dataclass
class InstanceStats:
    """Per-file instance counts plus corpus-level descriptive statistics."""

    per_file: dict[Path, int] = field(default_factory=dict)
    combinations: dict[tuple[str, ...], int] = field(default_factory=dict)

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
        import statistics

        values = list(self.per_file.values())
        if len(values) < 2:
            return None
        return statistics.stdev(values)


def instance_statistics(
    corpus: Corpus,
    instances: list[Instance],
    breakdown: bool = False,
) -> InstanceStats:
    """Aggregate ``instances`` over the corpus.

    Every file of the corpus participates - files without any instance count as
    ``0`` - so the mean is taken across all files.  When ``breakdown`` is true the
    counts per matched label combination are filled in as well.
    """
    stats = InstanceStats()
    for path in corpus.files:
        stats.per_file[path] = 0
    for inst in instances:
        stats.per_file[inst.file] = stats.per_file.get(inst.file, 0) + 1
        if breakdown:
            combo = inst.label_combination()
            stats.combinations[combo] = stats.combinations.get(combo, 0) + 1
    return stats
