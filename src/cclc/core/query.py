"""Visual-query model and evaluator over annotation co-occurrence.

A query combines *terms* (``tier = label``) with AND / OR / NOT and a temporal
constraint, and produces *instances* ("compounds"): sets of co-occurring
annotations.  The engine is pure Python and unit-tested.

Semantics (see PLAN.md sections 7.1 and 10):

* **Reference point** - distance between two annotations is the absolute
  difference of a selectable point: ``begin``, ``mid`` or ``end``.
* **Anchor** - the first non-negated term (depth-first) is the anchor.  Each
  anchor annotation yields at most one instance in the default ``"anchor"``
  counting mode; ``"combinations"`` emits every satisfying tuple instead.
* **Positive term** - satisfied when a matching annotation lies within the max
  distance of the anchor (or satisfies the active interval relation).
* **NOT (near-the-anchor rule)** - a negated term/group rejects the instance iff
  a matching annotation lies within the max distance of the anchor annotation.
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


def _compatible(
    anchor: Annotation, cand: Annotation, query: Query, relation: str | None
) -> bool:
    if relation is not None:
        return _relation_holds(relation, anchor, cand)
    dist = _distance(anchor, cand, query.reference_point)
    return dist is not None and dist <= query.max_distance_ms


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
        if query.counting_mode == "combinations":
            out.extend(_combinations_for_anchor(query.root, anchor, anchor_term, doc, path, query))
        else:
            matched: dict[str, Annotation] = {}
            ok = _satisfied(query.root, anchor, doc, query, None, matched)
            if ok:
                matched[anchor_term.key()] = anchor
                out.append(_make_instance(path, matched, query))
    return _dedupe(out)


def _satisfied(
    node: Node,
    anchor: Annotation,
    doc: ElanDocument,
    query: Query,
    relation: str | None,
    matched: dict[str, Annotation],
) -> bool:
    """Evaluate ``node`` for one anchor; record matched positive annotations."""
    if isinstance(node, Term):
        candidates = _term_matches(node, anchor, doc, query, relation)
        if node.negated:
            return not candidates
        if not candidates:
            return False
        matched[node.key()] = candidates[0]
        return True

    active = node.relation if node.relation is not None else relation
    if node.op == "AND":
        local: dict[str, Annotation] = {}
        for child in node.children:
            if not _satisfied(child, anchor, doc, query, active, local):
                ok = False
                break
        else:
            ok = True
        result = ok
    else:  # OR
        result = False
        local = {}
        for child in node.children:
            branch: dict[str, Annotation] = {}
            if _satisfied(child, anchor, doc, query, active, branch):
                local = branch
                result = True
                break

    if node.negated:
        return not result
    if result:
        matched.update(local)
    return result


def _term_matches(
    term: Term,
    anchor: Annotation,
    doc: ElanDocument,
    query: Query,
    relation: str | None,
) -> list[Annotation]:
    """Annotations on ``term``'s tier matching its label and compatible w/ anchor.

    Sorted by distance to the anchor so the nearest is first.
    """
    tier = doc.tiers.get(term.tier)
    if tier is None:
        return []
    matches: list[tuple[int, Annotation]] = []
    for ann in tier.annotations:
        if not ann.value or not label_matches(ann.value, term.label, case_sensitive=True):
            continue
        if not _compatible(anchor, ann, query, relation):
            continue
        dist = _distance(anchor, ann, query.reference_point)
        matches.append((dist if dist is not None else 1 << 30, ann))
    matches.sort(key=lambda t: t[0])
    return [ann for _, ann in matches]


def _combinations_for_anchor(
    node: Node,
    anchor: Annotation,
    anchor_term: Term,
    doc: ElanDocument,
    path: Path,
    query: Query,
) -> list[Instance]:
    """Expand every satisfying tuple of positive matches for one anchor.

    Supported for AND-dominant trees; OR groups contribute their union of
    candidates.  NOT conditions are checked exactly as in anchor mode.
    """
    # First confirm the expression is satisfiable at all (and that NOTs pass).
    probe: dict[str, Annotation] = {}
    if not _satisfied(node, anchor, doc, query, None, probe):
        return []

    # Collect candidate lists for each positive term reachable without negation.
    positive_terms: list[Term] = []
    _collect_positive_terms(node, negated_ctx=False, out=positive_terms)

    candidate_lists: list[tuple[str, list[Annotation]]] = []
    for term in positive_terms:
        if term.key() == anchor_term.key():
            candidate_lists.append((term.key(), [anchor]))
            continue
        cands = _term_matches(term, anchor, doc, query, None)
        if not cands:
            return []
        candidate_lists.append((term.key(), cands))

    # Cartesian product over the candidate lists.
    combos: list[dict[str, Annotation]] = [{}]
    for key, cands in candidate_lists:
        combos = [dict(c, **{key: ann}) for c in combos for ann in cands]

    return [_make_instance(path, combo, query) for combo in combos]


def _collect_positive_terms(node: Node, negated_ctx: bool, out: list[Term]) -> None:
    if isinstance(node, Term):
        if not (node.negated or negated_ctx):
            if all(t.key() != node.key() for t in out):
                out.append(node)
        return
    child_ctx = negated_ctx != node.negated
    for child in node.children:
        _collect_positive_terms(child, child_ctx, out)


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
