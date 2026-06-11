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
3. **Query View** — visually build complex queries over one corpus's annotations (labels from
   tiers combined with at least AND / OR / NOT, plus a maximum temporal distance the
   annotations may be away from each other), step through the matched instances one by one in
   a tier timeline (query tiers + freely selectable additional tiers), and feed the selected
   instances into the same descriptive statistics as the Analysis View.

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

### 3.1 Licensing — the project stays MIT

- The repository is MIT-licensed and **can stay MIT**. PySide6 is **LGPLv3**, not GPL: the
  LGPL explicitly allows applications that merely *use* the library to keep their own license
  (MIT, proprietary, …). Only PyQt6 (GPLv3 / commercial) would force a license change — that
  is exactly why this plan uses PySide6 and not PyQt6.
- `import PySide6` loads Qt as shared libraries at runtime (dynamic linking), which is the
  LGPL-compliant usage mode. Obligations, all easy to meet:
  1. don't ship a modified Qt/PySide6 (we don't — plain pip dependency);
  2. users must be able to replace the Qt libraries with their own build — trivially true for
     a pip-installed app, and for the PyInstaller stretch goal it is satisfied by using
     **one-folder mode** (Qt libraries remain separate, replaceable files);
  3. acknowledge the use of Qt/PySide6 under LGPLv3 (README / About dialog) and include a
     copy of or link to the LGPLv3 text.
- Caution for future features: a few Qt **add-on** modules are GPL-only (e.g. Qt Charts,
  Qt Data Visualization) and must be avoided — the stretch-goal chart therefore uses
  matplotlib (BSD-style) instead.
- Other dependencies: pympi-ling is MIT, matplotlib is BSD-style — both MIT-compatible.

## 4. Architecture

Strict separation: **core (parsing + analysis) is pure Python with no Qt imports**, fully unit
tested; the UI layer only renders and delegates. Layout (src style):

```
src/cclc/
├── main.py                  # entry point: QApplication + MainWindow
├── core/
│   ├── elan_document.py     # ElanDocument: parse one .eaf → tiers, annotations, CVs
│   ├── corpus.py            # Corpus, CorpusProject (the internal data structure)
│   ├── analysis.py          # pure functions: counts, mean/stdev, coverage
│   └── query.py             # query AST (term/AND/OR/NOT + max distance), evaluator → instances
├── ui/
│   ├── main_window.py       # QMainWindow with QTabWidget: "Corpora" | "Analysis" | "Query"
│   ├── config_view.py       # View 1: filesystem panel + corpora tree, drag & drop
│   ├── analysis_view.py     # View 2: selection form + result tables
│   ├── query_view.py        # View 3: visual query builder + instance browser + statistics
│   └── models.py            # Qt item models (corpora tree, result tables)
└── tests/
    ├── data/                # small fixture .eaf files (incl. CVs, edge cases)
    ├── test_elan_document.py
    ├── test_corpus.py
    ├── test_analysis.py
    └── test_query.py
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

@dataclass
class Instance:               # one query match ("compound"), produced by core/query.py
    file: Path
    matched: dict[str, Annotation]   # term id → matched annotation
    start_ms: int                    # min start over the matched annotations
    end_ms: int                      # max end over the matched annotations
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

## 7. View 3 — Query & instance browser

Third tab ("Query"). Visually composed boolean queries over the annotations of **one corpus**
with a temporal proximity constraint; the matches ("instances") can be stepped through one by
one and then handed over to the same descriptive statistics as in View 2.

```
┌─ Corpora ─┬─ Analysis ─┬─ Query ─────────────────────────────────────┐
│ Corpus: [Corpus A ▾]  Max distance: [2000] ms  measured at [begin ▾] │
│ ── Query builder ──────────────────────────────────────────────────  │
│ │ ALL of                                        [+ term] [+ group] │ │
│ │ ├─ Gesture_L = “point”                                     NOT ☐ │ │
│ │ ├─ ANY of                                                  NOT ☐ │ │
│ │ │   ├─ Head = “nod”                                              │ │
│ │ │   └─ Head = “shake”                                            │ │
│ │ └─ Speech = “overlap”                                      NOT ☑ │ │
│ │  ≙  point AND (nod OR shake) AND NOT overlap, ≤2000ms @begin     │ │
│ [Run query]                                 87 instances in 9 files  │
│ ── Instances ───────────────────────  [◀ Prev]  12 / 87  [Next ▶] ─  │
│ │ session03.eaf    00:04:12.3 – 00:04:13.1             selected ☑ │ │
│ │ Gesture_L ───[point]───────────────────                         │ │
│ │ Head      ─────────[nod]───────────────    (context ± 2 s)      │ │
│ │ Speech    ───────────────[laugh]───────                         │ │
│ │ visible tiers: ☑ query tiers   ☐ Gesture_R  ☑ Speech  …         │ │
│ [Select all] [Deselect all]      [Use 64 selected → statistics]      │
│ │ File │ Instances │ …    Σ · mean/file · σ          [Export CSV] │ │
└──────────────────────────────────────────────────────────────────────┘
```

### 7.1 Query model (`core/query.py`)

- **Term** = *tier + label*, e.g. `Gesture_L = "point"`. Labels are offered from the tier's
  dictionary (same logic as View 2); for non-dictionary tiers a free-text equals match is
  allowed.
- **Operators:** AND-groups ("ALL of") and OR-groups ("ANY of") with arbitrary nesting;
  **NOT** is a toggle on any term or group. A query must contain at least one non-negated
  term.
- **Max distance `D` (ms) + reference point:** set per query (optional override per
  AND-group). The reference point is selectable as **beginning**, **midpoint** or **end** of
  an annotation; the distance between two annotations (on different tiers) is the absolute
  difference of their reference points — e.g. for "beginning": `|start_B − start_A|`. All
  annotations forming one instance must be **pairwise** ≤ `D` apart ("maximally away from
  each other").
  *Example (reference = beginning):* annotation A on tier *a* starts at 5 ms, annotation B on
  tier *b* at 1000 ms → distance 995 ms. With `D` = 500 ms the pair is **not** a compound;
  with `D` = 1000 ms it is found as a match/compound.
- **Instance (compound) & counting semantics:** the first non-negated term is the *anchor*.
  Each annotation matching the anchor yields **at most one instance**: the evaluator searches
  the anchor's `D`-neighborhood for the nearest annotations satisfying the remaining
  expression. `point AND nod` therefore counts *point gestures that have a nod nearby* —
  interpretable counts instead of a combinatorial pair explosion (the alternative "every
  satisfying combination" is listed as an open question).
- **NOT semantics:** a negated term/group is satisfied iff **no** matching annotation lies
  within distance `D` of the instance's positive annotations.
- Evaluation per file (annotations sorted by time, windowed join), concatenated over the
  corpus, run in a background thread; result: `list[Instance]`.

### 7.2 Visual query builder

- A tree that mirrors the expression: group nodes ("ALL of" / "ANY of") and term rows
  (tier combo + label combo + NOT checkbox); buttons *add term*, *add group*, *delete*;
  drag to re-order or re-nest.
- The equivalent readable expression is displayed live underneath
  (`point AND (nod OR shake) AND NOT overlap, within 2000 ms measured at beginning`).
- Validation with inline messages (only-NOT query, empty group, tier missing in corpus).
- **Saved queries:** name + expression stored in the project JSON (should-have).

### 7.3 Instance browser

- Result table: instance number, file, time range, matched label per term; status line with
  the total ("87 instances in 9 files").
- **One-by-one navigation:** Prev/Next buttons and arrow keys, position indicator "12 / 87".
- Detail panel = **timeline excerpt** (custom-painted `QGraphicsView`): one row per visible
  tier, annotation boxes with their labels, the instance's matched annotations highlighted,
  context padding ±2 s (configurable).
- **Visible tiers:** the tiers used in the query are always shown; any additional tiers of
  the corpus can be toggled on/off via a checklist.
- Media playback is out of scope for v1 (stretch goal: "open file in ELAN" shortcut).

### 7.4 Hand-off to statistics ("next analysis step")

- Every result row carries a checkbox (default: selected); *Select all / Deselect all*;
  instances can also be (de)selected while stepping through them in the browser.
- "Use selected instances → statistics" computes — re-using the View 2 components and the
  definitions of §6.2, with instances in place of single labels — over the **selected**
  instances: instances per file, Σ total, mean per file, sample σ (files of the corpus
  without any instance count as 0); optional breakdown by matched label combination;
  CSV export.
- The selection is kept as a plain `list[Instance]`, so later steps (query chaining, further
  exports) can build on it.

## 8. Testing strategy

- Hand-crafted fixture `.eaf` files in `tests/data/`: minimal file with 2 CV tiers + 1 free
  tier; file with EAF 2.8 `CV_ENTRY_ML`; file with out-of-dictionary values; file missing a
  tier; an invalid XML file; a file with precisely timed annotations across several tiers for
  the distance/co-occurrence cases.
- Unit tests for parsing (tiers/CV extraction), corpus operations (move/duplicate rules,
  JSON round-trip), and every metric in §6.2 against hand-computed values.
- Query engine tests (§7.1): AND/OR/NOT, nesting, every reference-point mode
  (beginning/midpoint/end), distance boundary cases (distance exactly `D`; the 5 ms vs
  1000 ms example from §7.1), NOT exclusion within the window, anchored counting, multi-file
  concatenation — all against hand-computed instance lists.
- UI smoke test (optional, `pytest-qt`): build main window, simulate a drop, switch views.

## 9. Milestones

| # | Deliverable | Contents |
|---|---|---|
| 1 | Scaffolding | `pyproject.toml` (PySide6, pympi-ling; dev: pytest, ruff), package skeleton, fixtures |
| 2 | Core: ELAN parsing | `ElanDocument` + CV extraction + tests |
| 3 | Core: corpora + analysis | `CorpusProject`, all §6.2 metrics + tests |
| 4 | UI shell | `MainWindow`, three tabs, status bar |
| 5 | View 1 | filesystem panel, corpora tree, full drag & drop, corpus management |
| 6 | View 2 | selection form, counts table + Σ/mean/σ, coverage table + mean coverage |
| 7 | Core: query engine | query AST (AND/OR/NOT, max distance), anchored evaluator, `Instance` + tests |
| 8 | View 3 | visual query builder, instance browser with tier timeline, statistics hand-off |
| 9 | Polish | project save/load (incl. saved queries), CSV export, background parsing/caching, error reporting |
| 10 | (Stretch) | Ctrl+drag copy, multi-label selection, bar chart (matplotlib), "open in ELAN", PyInstaller build (one-folder mode, see §3.1) |

Milestones 2–3 and 7 are pure-Python and reviewable independently of any UI work.

## 10. Open questions

1. Coverage is defined per *(file, tier)* with the mean taken per tier across files — is an
   additional per-file aggregate (union of labels over all tiers of the file) wanted?
2. Should out-of-dictionary annotation values optionally be counted as part of the total
   annotation count (currently: shown separately, excluded from metrics)?
3. Label matching case-sensitive (current default) or case-insensitive?
4. Files missing a tier: keep "count as 0" (current default, n = all files) or exclude them
   from mean/σ?
5. UI language English only, or German as well?
6. The distance reference point (beginning/midpoint/end) is one global choice per query
   (current default) — should it additionally be selectable per term (e.g. *end* of A to
   *beginning* of B), or extended by interval relations such as "overlaps"?
7. Instance counting: one instance per anchor annotation (current default) or every
   satisfying combination of annotations?
8. Should NOT exclude matches only within the distance window `D` (current default), or
   whenever the negated label occurs anywhere in the file?
9. For the instance statistics (§7.4): are per-file instance counts sufficient, or is a
   breakdown by matched label combination needed as well?
