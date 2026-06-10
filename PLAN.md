# Plan: ELAN Corpus Label Comparer

A Python desktop application for organizing ELAN annotation files (`.eaf`) into corpora via
drag & drop, and for analyzing annotation counts and dictionary (controlled vocabulary)
coverage across those corpora.

---

## 1. Goal

1. **Configuration View** — select `.eaf` files from the filesystem and move them via drag &
   drop into named corpora (an internal data structure, files on disk are never moved), and
   drag files between corpora.
2. **Analysis View** — for every tier that follows a *dictionary* (in ELAN terms: a tier whose
   linguistic type references a **Controlled Vocabulary**), count annotations and compute
   coverage statistics over the files of a corpus.

## 2. Key domain mapping (ELAN → app)

| Requirement term | ELAN concept (`.eaf` XML) |
|---|---|
| File | `.eaf` document (EUDICO Annotation Format) |
| Tier | `<TIER TIER_ID=… LINGUISTIC_TYPE_REF=…>` — matched **by tier name** across files |
| Dictionary | `<CONTROLLED_VOCABULARY>` (CV); a tier "follows a dictionary" when its `<LINGUISTIC_TYPE>` has a `CONTROLLED_VOCABULARY_REF` |
| Label / annotation | `<ANNOTATION_VALUE>` of alignable or reference annotations on that tier (optionally validated via `CVE_REF` in EAF ≥ 2.8) |

## 3. Technology choices

| Concern | Choice | Rationale |
|---|---|---|
| UI toolkit | **PySide6 (Qt 6)** | First-class drag & drop (from OS file manager via `text/uri-list` and between widgets via internal move), mature model/view tables and trees, LGPL license. Tkinter would need the third-party `tkinterdnd2` for OS drops and has much weaker table widgets. |
| ELAN parsing | **pympi-ling** (`pympi.Elan.Eaf`) | De-facto standard for `.eaf` in Python (tiers, annotations, linguistic types, CVs). Fallback: a thin `xml.etree` parser of our own if pympi's CV handling is insufficient for EAF 2.8+ multi-language CVs (`CV_ENTRY_ML`/`CVE_VALUE`). |
| Statistics | stdlib `statistics` | Only mean / sample standard deviation needed; avoids a numpy dependency. |
| Persistence (project file) | JSON via stdlib | Corpora + file paths only; trivial schema. |
| Packaging / tooling | `pyproject.toml`, `pytest`, `ruff` | Standard, lightweight. |

Python ≥ 3.10.

## 4. Architecture

Strict separation: **core (parsing + analysis) is pure Python with no Qt imports**, fully unit
tested; the UI layer only renders and delegates. Layout (src style):

```
src/cclc/
├── main.py                  # entry point: QApplication + MainWindow
├── core/
│   ├── elan_document.py     # ElanDocument: parse one .eaf → tiers, annotations, CVs
│   ├── corpus.py            # Corpus, CorpusProject (the internal data structure)
│   └── analysis.py          # pure functions: counts, mean/stdev, coverage
├── ui/
│   ├── main_window.py       # QMainWindow with QTabWidget: "Corpora" | "Analysis"
│   ├── config_view.py       # View 1: filesystem panel + corpora tree, drag & drop
│   ├── analysis_view.py     # View 2: selection form + result tables
│   └── models.py            # Qt item models (corpora tree, result tables)
└── tests/
    ├── data/                # small fixture .eaf files (incl. CVs, edge cases)
    ├── test_elan_document.py
    ├── test_corpus.py
    └── test_analysis.py
```

### 4.1 Core data model

```python
@dataclass
class Annotation:
    value: str
    start_ms: int | None      # None for ref annotations without own timing
    end_ms: int | None
    cve_ref: str | None       # CV entry id, if the file provides it (EAF ≥ 2.8)

@dataclass
class Tier:
    name: str                 # TIER_ID
    linguistic_type: str
    cv_id: str | None         # set ⇔ tier follows a dictionary
    annotations: list[Annotation]

@dataclass
class ElanDocument:           # immutable parse result of one .eaf
    path: Path
    tiers: dict[str, Tier]
    vocabularies: dict[str, list[str]]   # cv_id → ordered labels
    # cached by (path, mtime); re-parsed when the file changes on disk

@dataclass
class Corpus:
    name: str
    files: list[Path]         # ordered, no duplicates within one corpus

@dataclass
class CorpusProject:
    corpora: list[Corpus]
    # operations: add/rename/remove corpus, add file, move file (corpus → corpus),
    # remove file; save/load as JSON
```

Decisions:
- **Move semantics** between corpora (file leaves the source corpus), matching the request
  ("move them … into different corpora"). `Ctrl`+drag = copy is a cheap optional extra.
- A file may appear in several corpora (corpora are independent samples), but only once per
  corpus.
- Parsing is **lazy + cached**: adding a file to a corpus triggers a validation parse
  (headers/tiers) in a background thread (`QThreadPool`) so the UI never blocks; invalid or
  unreadable files are rejected with a visible error.

## 5. View 1 — Configuration (drag & drop)

```
┌─ Corpora ──────────────────┬─ Analysis ─────────────────────────────┐
│                                                                     │
│  Filesystem (*.eaf)          Corpora                                │
│  ┌───────────────────┐       ┌───────────────────────────────────┐  │
│  │ ▸ home            │       │ ▾ Corpus A            (12 files)  │  │
│  │   ▾ recordings    │  drag │     session01.eaf                 │  │
│  │     sess01.eaf    │ ────▶ │     session02.eaf                 │  │
│  │     sess02.eaf    │       │ ▾ Corpus B             (3 files)  │  │
│  │     notes.txt ✗   │       │     pilot01.eaf   ◀── drag between│  │
│  └───────────────────┘       └───────────────────────────────────┘  │
│  [Add files…]                [+ New corpus] [Rename] [Remove]       │
└─────────────────────────────────────────────────────────────────────┘
```

- **Left panel:** `QTreeView` + `QFileSystemModel`, name-filtered to `*.eaf`, drag enabled
  (provides `text/uri-list` for free).
- **Right panel:** corpora tree (custom `QAbstractItemModel`): top-level nodes = corpora,
  children = files. Accepts
  1. drops from the left panel,
  2. drops **directly from the OS file manager** (Explorer/Finder/Nautilus — same MIME type),
  3. internal moves of file nodes between corpus nodes (move semantics, duplicate-safe).
  Dropping onto a corpus node appends; dropping a folder adds all `.eaf` files inside
  (recursive). Corpus nodes themselves are not draggable into other corpora.
- **Fallbacks without DnD** (accessibility): "Add files…" → `QFileDialog`; context menu
  "Move to corpus ▸"; `Del` removes a file from a corpus.
- Corpus management: create (unique auto-name "Corpus N"), rename (inline edit), remove
  (confirmation dialog). File nodes show a tooltip with tier count / duration after parsing.
- **Project persistence:** File ▸ Save/Open project (`.json` with corpus names + absolute file
  paths); missing files are flagged on load, not silently dropped.

## 6. View 2 — Analysis

Scope of all analysis: one selected **corpus**; only tiers that follow a dictionary are offered.

```
┌─ Corpora ─┬─ Analysis ──────────────────────────────────────────────┐
│ Corpus: [Corpus A ▾]   Tier: [Gesture_L ▾]   Label: [point ▾]       │
│                                              ☑ break down per tier  │
│ ── Counts for label “point” ──────────────────────────────────────  │
│ │ File          │ Count │ (Gesture_L) │ (Gesture_R) │               │
│ │ session01.eaf │   14  │      9      │      5      │               │
│ │ session02.eaf │    3  │      3      │      0      │               │
│ │ Σ total: 17      mean/file: 8.50      σ (sample): 7.78 │          │
│                                                                     │
│ ── Dictionary coverage ───────────────────────────────────────────  │
│ │ File          │ Gesture_L      │ Gesture_R      │                 │
│ │ session01.eaf │ 80 % (8/10)    │ 40 % (4/10)    │                 │
│ │ session02.eaf │ 100 % (10/10)  │ 30 % (3/10)    │                 │
│ │ mean coverage │ 90 %           │ 35 %           │                 │
│                                                  [Export CSV]      │
└─────────────────────────────────────────────────────────────────────┘
```

### 6.1 Selection form
- **Corpus** combo box.
- **Tier** combo box: union of dictionary-following tier names across the corpus's files
  (matched by tier name); shows the associated CV name and dictionary size.
- **Label** combo box: the entries of that tier's dictionary. Dictionary = **union of the CV
  entries across all files** of the corpus for that tier (files may carry slightly different
  CV versions; the union keeps every label selectable — flagged in the UI when files disagree).
- Toggle "per tier breakdown" — adds one count column per tier when several tiers share the
  selected dictionary (e.g. `Gesture_L` / `Gesture_R`).

### 6.2 Metrics (exact definitions, implemented in `core/analysis.py`)

For corpus `C` with files `f₁…fₙ`, tier `T`, dictionary `D_T`, selected label `L ∈ D_T`:

- **Count per file:** `count(fᵢ) = |{a ∈ annotations(fᵢ, T) : a.value == L}|`
  (match via `CVE_REF` when present, else exact string match — case-sensitive).
- **Total:** `Σ count(fᵢ)` over the corpus.
- **Mean:** `Σ count(fᵢ) / n`.
- **Standard deviation:** sample stdev (n−1) of the per-file counts; shown as "n/a" for n < 2.
- **Coverage per file and tier:**
  `coverage(f, T) = |{ℓ ∈ D_T : ℓ occurs ≥ 1× in f on T}| / |D_T| · 100 %`
  → 100 % ⇔ every dictionary label is annotated at least once in that file & tier.
- **Mean coverage (per tier):** `(1/n) · Σ coverage(fᵢ, T)` across all files of the corpus.

Edge-case policy (defaults, each surfaced in the UI rather than hidden):
- File lacks tier `T` → shown as "—", **treated as count 0 / coverage 0 %** and included in
  mean/σ/mean-coverage (n = corpus size, per the requirement "across all files"); a status
  hint reports how many files lack the tier.
- Annotation value on a CV tier that is **not in the dictionary** (typo/free text) → excluded
  from counts and coverage, but reported in an "out-of-dictionary" column so data problems
  stay visible.
- Empty annotation values are ignored.
- External CVs (`.ecv` via `EXT_REF`): resolve relative to the `.eaf` location; if
  unresolvable, warn and fall back to labels observed via `CVE_REF`/values.

### 6.3 Behavior
- Recompute automatically on corpus/tier/label change; parsing runs in the background with a
  progress indicator (results cached per file mtime, so recomputation is instant afterwards).
- **Export CSV** for both tables (counts and coverage).

## 7. Testing strategy

- Hand-crafted fixture `.eaf` files in `tests/data/`: minimal file with 2 CV tiers + 1 free
  tier; file with EAF 2.8 `CV_ENTRY_ML`; file with out-of-dictionary values; file missing a
  tier; an invalid XML file.
- Unit tests for parsing (tiers/CV extraction), corpus operations (move/duplicate rules,
  JSON round-trip), and every metric in §6.2 against hand-computed values.
- UI smoke test (optional, `pytest-qt`): build main window, simulate a drop, switch views.

## 8. Milestones

| # | Deliverable | Contents |
|---|---|---|
| 1 | Scaffolding | `pyproject.toml` (PySide6, pympi-ling; dev: pytest, ruff), package skeleton, fixtures |
| 2 | Core: ELAN parsing | `ElanDocument` + CV extraction + tests |
| 3 | Core: corpora + analysis | `CorpusProject`, all §6.2 metrics + tests |
| 4 | UI shell | `MainWindow`, two tabs, status bar |
| 5 | View 1 | filesystem panel, corpora tree, full drag & drop, corpus management |
| 6 | View 2 | selection form, counts table + Σ/mean/σ, coverage table + mean coverage |
| 7 | Polish | project save/load, CSV export, background parsing/caching, error reporting |
| 8 | (Stretch) | Ctrl+drag copy, multi-label selection, bar chart (matplotlib), PyInstaller build |

Milestones 2–3 are pure-Python and reviewable independently of any UI work.

## 9. Open questions

1. Coverage is defined per *(file, tier)* with the mean taken per tier across files — is an
   additional per-file aggregate (union of labels over all tiers of the file) wanted?
2. Should out-of-dictionary annotation values optionally be counted as part of the total
   annotation count (currently: shown separately, excluded from metrics)?
3. Label matching case-sensitive (current default) or case-insensitive?
4. Files missing a tier: keep "count as 0" (current default, n = all files) or exclude them
   from mean/σ?
5. UI language English only, or German as well?
