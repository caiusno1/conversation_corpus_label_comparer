"""Parsing of ELAN ``.eaf`` files into a lightweight, dependency-free model.

Only the parts relevant to this application are extracted: tiers, their
annotations (with timing), the linguistic type of each tier and the controlled
vocabularies ("dictionaries").  Parsing uses the standard-library XML parser so
the core has no third-party runtime dependency and stays fully unit-testable.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path


class ElanParseError(Exception):
    """Raised when a file cannot be parsed as an ELAN ``.eaf`` document."""


@dataclass(frozen=True)
class Annotation:
    """A single annotation on a tier.

    ``start_ms``/``end_ms`` may be ``None`` for unaligned annotations whose time
    slots carry no ``TIME_VALUE``.
    """

    value: str
    start_ms: int | None
    end_ms: int | None
    cve_ref: str | None = None

    def reference_point(self, mode: str) -> int | None:
        """Return the time point used for distance computation.

        ``mode`` is one of ``"begin"``, ``"mid"`` or ``"end"``.  Returns ``None``
        when the annotation has no timing information.
        """
        if self.start_ms is None or self.end_ms is None:
            return None
        if mode == "begin":
            return self.start_ms
        if mode == "end":
            return self.end_ms
        if mode == "mid":
            return (self.start_ms + self.end_ms) // 2
        raise ValueError(f"unknown reference point mode: {mode!r}")


@dataclass
class Tier:
    """A tier and its annotations.

    ``cv_id`` is set exactly when the tier's linguistic type references a
    controlled vocabulary - i.e. when the tier "follows a dictionary".
    """

    name: str
    linguistic_type: str
    cv_id: str | None
    annotations: list[Annotation] = field(default_factory=list)

    @property
    def follows_dictionary(self) -> bool:
        return self.cv_id is not None


@dataclass
class ElanDocument:
    """Immutable parse result of a single ``.eaf`` file."""

    path: Path
    tiers: dict[str, Tier]
    vocabularies: dict[str, list[str]]

    # --- convenience accessors -------------------------------------------------

    def tier_names(self) -> list[str]:
        return list(self.tiers.keys())

    def dictionary_tier_names(self) -> list[str]:
        return [name for name, tier in self.tiers.items() if tier.follows_dictionary]

    def dictionary_for_tier(self, tier_name: str) -> list[str]:
        """Return the controlled-vocabulary labels backing ``tier_name``.

        Empty list when the tier does not follow a dictionary or the CV is
        unknown.
        """
        tier = self.tiers.get(tier_name)
        if tier is None or tier.cv_id is None:
            return []
        return self.vocabularies.get(tier.cv_id, [])

    # --- parsing ---------------------------------------------------------------

    @classmethod
    def parse(cls, path: str | Path) -> ElanDocument:
        path = Path(path)
        try:
            tree = ET.parse(path)
        except ET.ParseError as exc:
            raise ElanParseError(f"{path}: invalid XML ({exc})") from exc
        except OSError as exc:
            raise ElanParseError(f"{path}: cannot read file ({exc})") from exc

        root = tree.getroot()
        if not root.tag.endswith("ANNOTATION_DOCUMENT"):
            raise ElanParseError(f"{path}: not an ELAN document (root <{root.tag}>)")

        time_slots = _parse_time_slots(root)
        vocabularies = _parse_vocabularies(root)
        type_to_cv = _parse_linguistic_types(root)
        tiers = _parse_tiers(root, time_slots, type_to_cv)

        return cls(path=path, tiers=tiers, vocabularies=vocabularies)


def _parse_time_slots(root: ET.Element) -> dict[str, int | None]:
    slots: dict[str, int | None] = {}
    order = root.find("TIME_ORDER")
    if order is None:
        return slots
    for slot in order.findall("TIME_SLOT"):
        slot_id = slot.get("TIME_SLOT_ID")
        if slot_id is None:
            continue
        value = slot.get("TIME_VALUE")
        slots[slot_id] = int(value) if value is not None else None
    return slots


def _parse_vocabularies(root: ET.Element) -> dict[str, list[str]]:
    """Extract controlled vocabularies, supporting EAF < 2.8 and >= 2.8 forms.

    Labels are returned in document order with duplicates removed.
    """
    vocabularies: dict[str, list[str]] = {}
    for cv in root.findall("CONTROLLED_VOCABULARY"):
        cv_id = cv.get("CV_ID")
        if cv_id is None:
            continue
        labels: list[str] = []

        # EAF < 2.8: <CV_ENTRY>value</CV_ENTRY>
        for entry in cv.findall("CV_ENTRY"):
            text = (entry.text or "").strip()
            if text:
                labels.append(text)

        # EAF >= 2.8: <CV_ENTRY_ML><CVE_VALUE>value</CVE_VALUE></CV_ENTRY_ML>
        for entry in cv.findall("CV_ENTRY_ML"):
            value_el = entry.find("CVE_VALUE")
            text = (value_el.text or "").strip() if value_el is not None else ""
            if text:
                labels.append(text)

        # Deduplicate while preserving order.
        seen: set[str] = set()
        ordered: list[str] = []
        for label in labels:
            if label not in seen:
                seen.add(label)
                ordered.append(label)
        vocabularies[cv_id] = ordered
    return vocabularies


def _parse_linguistic_types(root: ET.Element) -> dict[str, str | None]:
    """Map ``LINGUISTIC_TYPE_ID`` -> controlled-vocabulary id (or ``None``)."""
    mapping: dict[str, str | None] = {}
    for lt in root.findall("LINGUISTIC_TYPE"):
        type_id = lt.get("LINGUISTIC_TYPE_ID")
        if type_id is None:
            continue
        mapping[type_id] = lt.get("CONTROLLED_VOCABULARY_REF")
    return mapping


def _resolve_timing(
    ann_el: ET.Element,
    time_slots: dict[str, int | None],
    alignable_by_id: dict[str, tuple[int | None, int | None]],
) -> tuple[int | None, int | None]:
    """Resolve the (start, end) of an annotation element.

    Alignable annotations read their two time-slot references directly.  Ref
    annotations inherit the timing of the alignable annotation they ultimately
    point to (the parent chain is pre-resolved in ``alignable_by_id``).
    """
    alignable = ann_el.find("ALIGNABLE_ANNOTATION")
    if alignable is not None:
        ts1 = alignable.get("TIME_SLOT_REF1")
        ts2 = alignable.get("TIME_SLOT_REF2")
        return time_slots.get(ts1), time_slots.get(ts2)

    ref = ann_el.find("REF_ANNOTATION")
    if ref is not None:
        parent_id = ref.get("ANNOTATION_REF")
        if parent_id is not None and parent_id in alignable_by_id:
            return alignable_by_id[parent_id]
    return None, None


def _parse_tiers(
    root: ET.Element,
    time_slots: dict[str, int | None],
    type_to_cv: dict[str, str | None],
) -> dict[str, Tier]:
    # First pass: collect alignable annotation timings so ref annotations on
    # child tiers can inherit them regardless of tier order.
    alignable_by_id: dict[str, tuple[int | None, int | None]] = {}
    for alignable in root.iter("ALIGNABLE_ANNOTATION"):
        ann_id = alignable.get("ANNOTATION_ID")
        if ann_id is None:
            continue
        ts1 = alignable.get("TIME_SLOT_REF1")
        ts2 = alignable.get("TIME_SLOT_REF2")
        alignable_by_id[ann_id] = (time_slots.get(ts1), time_slots.get(ts2))

    # Resolve ref-annotation chains so a ref-of-a-ref still inherits real timing.
    ref_parent: dict[str, str] = {}
    for ref in root.iter("REF_ANNOTATION"):
        ann_id = ref.get("ANNOTATION_ID")
        parent = ref.get("ANNOTATION_REF")
        if ann_id is not None and parent is not None:
            ref_parent[ann_id] = parent

    def root_timing(ann_id: str) -> tuple[int | None, int | None]:
        seen: set[str] = set()
        current = ann_id
        while current in ref_parent and current not in seen:
            seen.add(current)
            current = ref_parent[current]
        return alignable_by_id.get(current, (None, None))

    tiers: dict[str, Tier] = {}
    for tier_el in root.findall("TIER"):
        tier_id = tier_el.get("TIER_ID")
        if tier_id is None:
            continue
        ling_type = tier_el.get("LINGUISTIC_TYPE_REF") or ""
        cv_id = type_to_cv.get(ling_type)
        tier = Tier(name=tier_id, linguistic_type=ling_type, cv_id=cv_id)

        for ann_el in tier_el.findall("ANNOTATION"):
            inner = ann_el.find("ALIGNABLE_ANNOTATION")
            if inner is None:
                inner = ann_el.find("REF_ANNOTATION")
            if inner is None:
                continue
            value_el = inner.find("ANNOTATION_VALUE")
            value = (value_el.text or "").strip() if value_el is not None else ""
            cve_ref = inner.get("CVE_REF")

            if inner.tag == "ALIGNABLE_ANNOTATION":
                start, end = _resolve_timing(ann_el, time_slots, alignable_by_id)
            else:
                ann_id = inner.get("ANNOTATION_ID")
                start, end = root_timing(ann_id) if ann_id else (None, None)

            tier.annotations.append(
                Annotation(value=value, start_ms=start, end_ms=end, cve_ref=cve_ref)
            )
        tiers[tier_id] = tier
    return tiers
