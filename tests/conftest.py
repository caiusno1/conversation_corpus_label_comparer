"""Shared test helpers: a small builder that writes minimal ``.eaf`` files."""

from __future__ import annotations

from pathlib import Path

EAF_HEADER = '<?xml version="1.0" encoding="UTF-8"?>'


def build_eaf(
    tiers: dict[str, list[tuple[str, int, int]]],
    *,
    cvs: dict[str, list[str]] | None = None,
    tier_cv: dict[str, str] | None = None,
    ml_cv: bool = False,
) -> str:
    """Return an EAF XML string.

    ``tiers`` maps tier name -> list of (value, start_ms, end_ms).
    ``cvs`` maps cv id -> list of controlled-vocabulary labels.
    ``tier_cv`` maps tier name -> cv id (the tier then "follows a dictionary").
    ``ml_cv`` writes EAF >= 2.8 multilingual ``CV_ENTRY_ML`` entries.
    """
    cvs = cvs or {}
    tier_cv = tier_cv or {}

    # Build the time order from all distinct time values.
    times: set[int] = set()
    for anns in tiers.values():
        for _, start, end in anns:
            times.update((start, end))
    ordered = sorted(times)
    slot_id = {t: f"ts{i + 1}" for i, t in enumerate(ordered)}

    lines = [
        EAF_HEADER,
        '<ANNOTATION_DOCUMENT AUTHOR="" DATE="2026-01-01T00:00:00+00:00" '
        'FORMAT="3.0" VERSION="3.0">',
        '  <HEADER MEDIA_FILE="" TIME_UNITS="milliseconds"></HEADER>',
        "  <TIME_ORDER>",
    ]
    for t in ordered:
        lines.append(f'    <TIME_SLOT TIME_SLOT_ID="{slot_id[t]}" TIME_VALUE="{t}"/>')
    lines.append("  </TIME_ORDER>")

    ann_id = 0
    for tier_name, anns in tiers.items():
        ling_type = tier_cv.get(tier_name, "default-lt")
        lines.append(
            f'  <TIER LINGUISTIC_TYPE_REF="{ling_type}" TIER_ID="{tier_name}">'
        )
        for value, start, end in anns:
            ann_id += 1
            lines.append("    <ANNOTATION>")
            lines.append(
                f'      <ALIGNABLE_ANNOTATION ANNOTATION_ID="a{ann_id}" '
                f'TIME_SLOT_REF1="{slot_id[start]}" TIME_SLOT_REF2="{slot_id[end]}">'
            )
            lines.append(f"        <ANNOTATION_VALUE>{value}</ANNOTATION_VALUE>")
            lines.append("      </ALIGNABLE_ANNOTATION>")
            lines.append("    </ANNOTATION>")
        lines.append("  </TIER>")

    # Linguistic types: one per tier that references a CV, plus a default.
    declared: set[str] = set()
    for tier_name in tiers:
        lt = tier_cv.get(tier_name)
        if lt and lt not in declared:
            declared.add(lt)
            lines.append(
                f'  <LINGUISTIC_TYPE LINGUISTIC_TYPE_ID="{lt}" '
                f'CONTROLLED_VOCABULARY_REF="{lt}" TIME_ALIGNABLE="true"/>'
            )
    lines.append(
        '  <LINGUISTIC_TYPE LINGUISTIC_TYPE_ID="default-lt" TIME_ALIGNABLE="true"/>'
    )

    for cv_id, labels in cvs.items():
        lines.append(f'  <CONTROLLED_VOCABULARY CV_ID="{cv_id}">')
        for i, label in enumerate(labels):
            if ml_cv:
                lines.append(f'    <CV_ENTRY_ML CVE_ID="cve{i}">')
                lines.append(
                    f'      <CVE_VALUE DESCRIPTION="" LANG_REF="en">{label}</CVE_VALUE>'
                )
                lines.append("    </CV_ENTRY_ML>")
            else:
                lines.append(f'    <CV_ENTRY DESCRIPTION="">{label}</CV_ENTRY>')
        lines.append("  </CONTROLLED_VOCABULARY>")

    lines.append("</ANNOTATION_DOCUMENT>")
    return "\n".join(lines)


def write_eaf(path: Path, **kwargs) -> Path:
    path.write_text(build_eaf(**kwargs), encoding="utf-8")
    return path
