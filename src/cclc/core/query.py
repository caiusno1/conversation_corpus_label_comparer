"""Visual-query model and evaluator over annotation co-occurrence.

A query combines *terms* (``tier = label``) with AND / OR / NOT and a temporal
constraint, and produces *instances* ("compounds"): sets of co-occurring
annotations.  The engine is pure Python and unit-tested.

Semantics (see PLAN.md sections 7.1 and 12):

* **Reference point** - distance between two annotations is the absolute
  difference of a selectable point: ``begin``, ``mid`` or ``end``.
* **Anchor** - the first non-negated term (depth-first) is the anchor.  Each
  anchor annotation yields at most one instance in the default ``"anchor"``
  counting mode; ``"combinations"`` emits every satisfying tuple instead.
* **Positive tuple (chain rule)** - the positive annotations of an instance
  form a temporal chain: sorted by their reference points, each *consecutive*
  pair is within the max distance.  ``A -900ms- B -900ms- C`` is therefore one
  compound at ``D = 1000`` even though A and C are 1800 ms apart.  Members
  matched under an interval relation are exempt from the chain (their relation
  already binds them to the anchor).  Candidates are tried nearest-to-the-anchor
  first, with backtracking when a later constraint rejects a choice.
* **NOT (near-any-member rule)** - a negated term/group rejects the instance iff
  a matching annotation lies within the max distance of **at least one** of the
  instance's positive annotations.
* **ALL (free variable)** - ``Term(tier, "", free=True)`` matches any non-empty
  label on the tier within the range of the rest of the compound and records the
  bound label in the instance (``A=... AND B=... AND ALL C``).  ``NOT ALL C``
  consequently rejects when *any* annotation on tier C is near the compound.
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
    """A single ``tier = label`` condition, optionally negated.

    With ``free=True`` the term is the **ALL operator** ("free variable"): it
    matches *any* non-empty label on ``tier`` that lies within the temporal
    range of the rest of the compound; the matched label is recorded in the
    instance.  ``label`` is ignored for free terms.
    """

    tier: str
    label: str
    negated: bool = False
    free: bool = False

    def key(self) -> str:
        return f"ALL {self.tier}" if self.free else f"{self.tier}={self.label}"


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
        """Sorted term keys; free (ALL) terms show their bound label."""
        parts = []
        for key in sorted(self.matched):
            if key.startswith("ALL "):
                parts.append(f"{key[4:]}={self.matched[key].value}")
            else:
                parts.append(key)
        return tuple(parts)


def free_term_keys(node: Node, negated_ctx: bool = False) -> list[str]:
    """Keys of the non-negated free (ALL) terms in depth-first order."""
    if isinstance(node, Term):
        if node.free and not (node.negated or negated_ctx):
            return [node.key()]
        return []
    child_ctx = negated_ctx != node.negated
    keys: list[str] = []
    for child in node.children:
        for key in free_term_keys(child, child_ctx):
            if key not in keys:
                keys.append(key)
    return keys


# --- textual query syntax (with brackets) -------------------------------------


class QueryParseError(Exception):
    """Raised when a textual query expression cannot be parsed."""


_BAREWORD = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-."


def _fmt_atom(text: str) -> str:
    """A tier/label token: bareword if simple, else a double-quoted string."""
    if text and all(ch in _BAREWORD for ch in text):
        return text
    escaped = text.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def to_query_string(node: Node) -> str:
    """Serialise an AST back to a parseable expression (round-trips with
    :func:`parse_query`)."""
    if isinstance(node, Term):
        if node.free:
            body = f"ALL {_fmt_atom(node.tier)}"
        else:
            body = f"{_fmt_atom(node.tier)} = {_fmt_atom(node.label)}"
        return f"NOT {body}" if node.negated else body

    joiner = " AND " if node.op == "AND" else " OR "
    parts = [to_query_string(child) for child in node.children]
    inner = joiner.join(parts)
    text = f"({inner})"
    if node.relation:
        text += f" [{node.relation}]"
    return f"NOT {text}" if node.negated else text


# Tokeniser: produces (kind, value) tuples. Kinds: WORD, STRING, and the literal
# punctuation '(', ')', '=', '[', ']'. Keywords are recognised from WORD tokens.
def _tokenize(text: str) -> list[tuple[str, str]]:
    tokens: list[tuple[str, str]] = []
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if ch.isspace():
            i += 1
        elif ch in "()=[]":
            tokens.append((ch, ch))
            i += 1
        elif ch in "&|!":  # symbol aliases for AND / OR / NOT
            tokens.append(("WORD", {"&": "AND", "|": "OR", "!": "NOT"}[ch]))
            i += 1
        elif ch == '"':
            i += 1
            buf = []
            while i < n and text[i] != '"':
                if text[i] == "\\" and i + 1 < n:
                    i += 1
                buf.append(text[i])
                i += 1
            if i >= n:
                raise QueryParseError("unterminated quoted string")
            i += 1  # closing quote
            tokens.append(("STRING", "".join(buf)))
        elif ch in _BAREWORD:
            j = i
            while j < n and text[j] in _BAREWORD:
                j += 1
            tokens.append(("WORD", text[i:j]))
            i = j
        else:
            raise QueryParseError(f"unexpected character {ch!r}")
    return tokens


class _Parser:
    """Recursive-descent parser: OR < AND < NOT < atom, with ( ) grouping."""

    _KEYWORDS = {"AND", "OR", "NOT", "ALL"}

    def __init__(self, tokens: list[tuple[str, str]]):
        self.tokens = tokens
        self.pos = 0

    def _peek(self) -> tuple[str, str] | None:
        return self.tokens[self.pos] if self.pos < len(self.tokens) else None

    def _next(self) -> tuple[str, str]:
        tok = self._peek()
        if tok is None:
            raise QueryParseError("unexpected end of expression")
        self.pos += 1
        return tok

    def _at_keyword(self, word: str) -> bool:
        tok = self._peek()
        return tok is not None and tok[0] == "WORD" and tok[1].upper() == word

    def parse(self) -> Group:
        node = self._or()
        if self._peek() is not None:
            raise QueryParseError(f"unexpected token {self._peek()[1]!r}")
        return node if isinstance(node, Group) else Group("AND", [node])

    def _or(self) -> Node:
        children = [self._and()]
        while self._at_keyword("OR"):
            self._next()
            children.append(self._and())
        return children[0] if len(children) == 1 else Group("OR", children)

    def _and(self) -> Node:
        children = [self._not()]
        while self._at_keyword("AND"):
            self._next()
            children.append(self._not())
        return children[0] if len(children) == 1 else Group("AND", children)

    def _not(self) -> Node:
        negate = False
        while self._at_keyword("NOT"):
            self._next()
            negate = not negate
        node = self._atom()
        if negate:
            node.negated = not node.negated
        return node

    def _atom(self) -> Node:
        tok = self._peek()
        if tok is None:
            raise QueryParseError("expected a term or '('")
        if tok[0] == "(":
            self._next()
            node = self._or()
            closing = self._next()
            if closing[0] != ")":
                raise QueryParseError("expected ')'")
            relation = self._optional_relation()
            if relation is not None:
                if isinstance(node, Term) or node.op != "AND":
                    node = Group("AND", [node])
                node.relation = relation
            return node
        return self._term()

    def _optional_relation(self) -> str | None:
        if self._peek() is not None and self._peek()[0] == "[":
            self._next()
            word = self._next()
            if word[0] != "WORD" or word[1] not in RELATIONS:
                raise QueryParseError(f"unknown relation {word[1]!r}")
            if self._next()[0] != "]":
                raise QueryParseError("expected ']'")
            return word[1]
        return None

    def _value(self) -> str:
        tok = self._next()
        if tok[0] == "STRING":
            return tok[1]
        if tok[0] == "WORD" and tok[1].upper() not in self._KEYWORDS:
            return tok[1]
        raise QueryParseError(f"expected a tier or label, got {tok[1]!r}")

    def _term(self) -> Term:
        if self._at_keyword("ALL"):
            self._next()
            return Term(tier=self._value(), label="", free=True)
        tier = self._value()
        eq = self._peek()
        if eq is None or eq[0] != "=":
            raise QueryParseError(f"expected '=' after tier {tier!r}")
        self._next()
        return Term(tier=tier, label=self._value())


def parse_query(text: str) -> Group:
    """Parse a textual boolean expression into a :class:`Group` AST.

    Grammar (case-insensitive keywords; ``&`` ``|`` ``!`` alias AND/OR/NOT)::

        expr   := or
        or     := and ( "OR" and )*
        and    := not ( "AND" not )*
        not    := "NOT"* atom
        atom   := "(" expr ")" [ "[" relation "]" ] | term
        term   := "ALL" name | name "=" name
        name   := bareword | "quoted string"

    Raises :class:`QueryParseError` on malformed input.
    """
    tokens = _tokenize(text)
    if not tokens:
        raise QueryParseError("empty expression")
    return _Parser(tokens).parse()


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


def _to_clauses(node: Node) -> list[list[Node]]:
    """Distribute the positive AND/OR structure of ``node`` into DNF clauses.

    Returns a list of clauses; each clause is a list of *literals* (positive
    terms, negated terms or negated groups) whose conjunction is one disjunct of
    the query.  This lets OR appear at any depth: every disjunct is then
    evaluated independently with its own anchor, instead of being forced to
    share the first term's anchor.  Negated nodes are kept atomic (the evaluator
    defers them), and a positive AND-group carrying an interval relation is also
    kept atomic so its relation context survives.
    """
    if isinstance(node, Term):
        return [[node]]
    if node.negated:
        return [[node]]  # atomic deferred literal
    if node.op == "AND":
        if node.relation is not None:
            return [[node]]  # keep the relation; evaluate the group as one literal
        clauses: list[list[Node]] = [[]]
        for child in node.children:
            child_clauses = _to_clauses(child)
            clauses = [c + extra for c in clauses for extra in child_clauses]
        return clauses
    # positive OR: union of the children's clauses
    out: list[list[Node]] = []
    for child in node.children:
        out.extend(_to_clauses(child))
    return out


def _evaluate_file(doc: ElanDocument, path: Path, query: Query) -> list[Instance]:
    out: list[Instance] = []
    for clause in _to_clauses(query.root):
        out.extend(_evaluate_clause(Group("AND", list(clause)), doc, path, query))
    return _dedupe(out)


def _evaluate_clause(
    root: Group, doc: ElanDocument, path: Path, query: Query
) -> list[Instance]:
    """Evaluate one purely conjunctive clause (anchored on its first term)."""
    anchor_term = _first_positive_term(root)
    if anchor_term is None:
        return []  # a disjunct with no positive term cannot be anchored
    anchors = _label_annotations(anchor_term, doc)

    n_positive = len(_positive_keys(root, False))
    max_span = max(0, n_positive - 1) * query.max_distance_ms

    out: list[Instance] = []
    for anchor in anchors:
        seed = {anchor_term.key(): anchor}
        for state, pending, rel_keys in _assignments(
            root, seed, (), frozenset(), doc, query, None, max_span
        ):
            chain = [ann for key, ann in state.items() if key not in rel_keys]
            if not _chain_ok(chain, query):
                continue
            positives = list(state.values())
            if any(_blocks(node, rel, positives, doc, query) for node, rel in pending):
                continue
            out.append(_make_instance(path, state, query))
            if query.counting_mode == "anchor":
                break
    return out


def _positive_keys(node: Node, negated_ctx: bool) -> set[str]:
    """Distinct keys of the terms that act positively in ``node``."""
    if isinstance(node, Term):
        return set() if (node.negated or negated_ctx) else {node.key()}
    child_ctx = negated_ctx != node.negated
    keys: set[str] = set()
    for child in node.children:
        keys |= _positive_keys(child, child_ctx)
    return keys


def _chain_ok(members: list[Annotation], query: Query) -> bool:
    """Whether the annotations form a temporal chain.

    Sorted by their reference points, every consecutive pair must be within the
    max distance.  ``A -900- B -900- C`` therefore passes at ``D = 1000`` even
    though A and C are 1800 ms apart.  Untimed members fail the chain.
    """
    if len(members) <= 1:
        return True
    points: list[int] = []
    for ann in members:
        point = ann.reference_point(query.reference_point)
        if point is None:
            return False
        points.append(point)
    points.sort()
    return all(
        b - a <= query.max_distance_ms
        for a, b in zip(points, points[1:], strict=False)
    )


def _assignments(
    node: Node,
    state: dict[str, Annotation],
    pending: tuple,
    rel_keys: frozenset[str],
    doc: ElanDocument,
    query: Query,
    relation: str | None,
    max_span: int,
):
    """Yield ``(state, pending, rel_keys)`` triples for the positive part of ``node``.

    ``state`` maps term keys to the chosen annotations (the anchor is
    pre-seeded).  Candidates are pre-filtered to lie within ``max_span`` of the
    anchor (``(n-1) * D`` - no chain member can be farther away); the actual
    chain constraint is validated on the completed tuple by the caller, which
    also gives backtracking: if the nearest candidate tuple is rejected, the
    next one is tried.  ``rel_keys`` records members matched under an interval
    relation - they are exempt from the chain check.  Negated nodes are deferred
    into ``pending`` with their relation context.
    """
    if isinstance(node, Term):
        if node.negated:
            yield state, (*pending, (node, relation)), rel_keys
            return
        if node.key() in state:
            yield state, pending, rel_keys
            return
        anchor = next(iter(state.values()))
        new_rel = rel_keys | {node.key()} if relation is not None else rel_keys
        for ann in _term_candidates(node, anchor, doc, query, relation, max_span):
            yield {**state, node.key(): ann}, pending, new_rel
        return

    if node.negated:
        yield state, (*pending, (node, relation)), rel_keys
        return

    active = node.relation if node.relation is not None else relation
    if node.op == "AND":
        states = [(state, pending, rel_keys)]
        for child in node.children:
            states = [
                extended
                for current in states
                for extended in _assignments(
                    child, current[0], current[1], current[2], doc, query, active, max_span
                )
            ]
            if not states:
                return
        yield from states
    else:  # OR: each child is a separate way to satisfy the group
        for child in node.children:
            yield from _assignments(
                child, state, pending, rel_keys, doc, query, active, max_span
            )


def _label_annotations(term: Term, doc: ElanDocument) -> list[Annotation]:
    """All annotations on ``term``'s tier whose value matches its label.

    A free (ALL) term matches every annotation with a non-empty value.
    """
    tier = doc.tiers.get(term.tier)
    if tier is None:
        return []
    if term.free:
        return [ann for ann in tier.annotations if ann.value]
    return [
        ann
        for ann in tier.annotations
        if ann.value and label_matches(ann.value, term.label, case_sensitive=True)
    ]


def _term_candidates(
    term: Term,
    anchor: Annotation,
    doc: ElanDocument,
    query: Query,
    relation: str | None,
    max_span: int,
) -> list[Annotation]:
    """Annotations matching ``term`` that could belong to the anchor's tuple.

    Relation mode: the Allen relation must hold against the anchor.  Distance
    mode: within ``max_span`` of the anchor (a coarse pre-filter; the chain
    constraint is checked on the completed tuple).  Sorted nearest-to-the-anchor
    first so the default anchor counting picks the closest satisfying tuple.
    """
    out: list[tuple[int, Annotation]] = []
    for ann in _label_annotations(term, doc):
        dist = _distance(ann, anchor, query.reference_point)
        if relation is not None:
            if not _relation_holds(relation, anchor, ann):
                continue
        elif dist is None or dist > max_span:
            continue
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
