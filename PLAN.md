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
4. **Interval View** — for a selectable tier of one corpus, collect all labels whose
   annotations lie within a fixed time interval (bounds slidable *and* directly enterable as
   numbers) and list the counts per file and label, plus the dictionary coverage restricted
   to that interval.
5. **Transitions View** — compute transition matrices over the label sequence of one or
   several dictionary tiers (cross-tier via the union of their dictionaries), per file and
   for the whole corpus: cell *(i, j)* = how often label *i* appeared immediately after
   label *j*, divided by all instances of label *i*.

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
│   ├── transitions.py       # transition matrices over merged label sequences
│   └── query.py             # query AST (term/AND/OR/NOT + max distance), evaluator → instances
├── ui/
│   ├── main_window.py       # QMainWindow, tabs: "Corpora" | "Analysis" | "Interval" | "Query"
│   ├── config_view.py       # View 1: filesystem panel + corpora tree, drag & drop
│   ├── analysis_view.py     # View 2: selection form + result tables
│   ├── query_view.py        # View 3: visual query builder + instance browser + statistics
│   ├── interval_view.py     # View 4: per-file label counts within a time window
│   ├── transitions_view.py  # View 5: label-to-label transition matrices
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
  (matched by tier name); shows the associated CV name and dictionary size. Tiers that are
  missing in at least one file of the corpus are marked and cannot be analyzed (see §6.2
  edge-case policy).
- **Label** combo box: the entries of that tier's dictionary. Dictionary = **union of the CV
  entries across all files** of the corpus for that tier (files may carry slightly different
  CV versions; the union keeps every label selectable — flagged in the UI when files disagree).
- Toggle "per tier breakdown" — adds one count column per tier when several tiers share the
  selected dictionary (e.g. `Gesture_L` / `Gesture_R`).

### 6.2 Metrics (exact definitions, implemented in `core/analysis.py`)

For corpus `C` with files `f₁…fₙ`, tier `T`, dictionary `D_T`, selected label `L ∈ D_T`:

- **Count per file:** `count(fᵢ) = |{a ∈ annotations(fᵢ, T) : a.value == L}|`
  (match via `CVE_REF` when present, else exact string match — case-sensitive by default,
  switchable via the UI toggle described below).
- **Total:** `Σ count(fᵢ)` over the corpus.
- **Mean:** `Σ count(fᵢ) / n`.
- **Standard deviation:** sample stdev (n−1) of the per-file counts; shown as "n/a" for n < 2.
- **Coverage per file and tier:**
  `coverage(f, T) = |{ℓ ∈ D_T : ℓ occurs ≥ 1× in f on T}| / |D_T| · 100 %`
  → 100 % ⇔ every dictionary label is annotated at least once in that file & tier.
- **Mean coverage (per tier):** `(1/n) · Σ coverage(fᵢ, T)` across all files of the corpus.

Edge-case policy (decided):
- **File lacks tier `T` → hard error.** An analysis (and likewise a query, §7.2) for tier `T`
  can only be run when **every file of the corpus contains the tier**; otherwise execution is
  blocked with an error stating exactly which file(s) lack the tier. If the tier exists but
  contains no annotation with the searched label, the count is simply **0** and the file
  participates normally in mean/σ/coverage.
- Annotation value on a CV tier that is **not in the dictionary** (typo/free text) → excluded
  from counts and coverage, but reported in an "out-of-dictionary" column so data problems
  stay visible.
- **Case sensitivity:** label matching is case-sensitive by default; a UI toggle switches the
  analysis views (counts, coverage, queries) to case-insensitive matching.
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
- **ALL operator (free variable):** `ALL <tier>` is a term without a fixed label — it matches
  *any* non-empty label on the tier that lies within the temporal range of the rest of the
  compound, and records the bound label in the instance. Example:
  `A="…" AND B="…" AND ALL C`. `NOT ALL C` consequently means "no annotation at all on
  tier C near the compound". In the term dialog the operator is a checkbox that disables the
  label field.
- **Operators:** AND-groups ("ALL of") and OR-groups ("ANY of") with arbitrary nesting;
  **NOT** is a toggle on any term or group. A query must contain at least one non-negated
  term.
- **Max distance `D` (ms) + reference point:** set per query (optional override per
  AND-group). The reference point is selectable as **beginning**, **midpoint** or **end** of
  an annotation; the distance between two annotations (on different tiers) is the absolute
  difference of their reference points — e.g. for "beginning": `|start_B − start_A|`. The
  annotations forming one instance must build a temporal **chain**: ordered by their
  reference points, every *consecutive* pair is ≤ `D` apart.
  *Example (reference = beginning):* annotation A on tier *a* starts at 5 ms, annotation B on
  tier *b* at 1000 ms → distance 995 ms. With `D` = 500 ms the pair is **not** a compound;
  with `D` = 1000 ms it is found as a match/compound.
  *Chain example:* `A —900ms— B —900ms— C` **is** one compound at `D` = 1000 ms — both
  consecutive gaps are within `D` — even though A and C are 1800 ms apart.
- **Interval relations (alternative constraint):** instead of a max distance, an AND-group
  can require an Allen-style relation between the matched annotations — *overlaps*,
  *contains/during*, *meets*, *starts together*, *ends together*. The max-distance constraint
  remains the default.
- **Instance (compound) & counting semantics:** the first non-negated term is the *anchor*.
  The positive annotations of an instance form a **chain** (consecutive members ≤ `D`, see
  above); members matched via an interval relation are bound by their relation instead.
  By default each annotation matching the anchor yields **at most one instance**: candidates
  are tried nearest-to-the-anchor first (with backtracking when a NOT rejects a choice).
  `point AND nod` therefore counts *point gestures that have a nod nearby* — interpretable
  counts instead of a combinatorial pair explosion. A **UI toggle** switches to *every
  satisfying combination* semantics (all valid tuples become instances).
- **NOT semantics (near-any-member rule, revised):** a negated term/group rejects an
  instance iff a matching annotation lies within max distance `D` of **at least one** of the
  instance's positive annotations — "selected only if no C is close enough to A or B".
  Examples (`D` = 1000 ms):
  - `A AND NOT B`: B 10 ms from A → rejected; B 1005 ms from A → instance kept.
  - `A AND B AND NOT C` (tuple A, B): C 600 ms from A → rejected even though C is 1100 ms
    from B; C 100 ms from B → rejected even though C is 1100 ms from A; C at least `D`
    away from **both** A and B → instance kept.
  - If the positive part already fails (e.g. A and B more than `D` apart), no compound
    exists in the first place — NOT never needs to be evaluated.
- Evaluation per file (annotations sorted by time, windowed join), concatenated over the
  corpus, run in a background thread; result: `list[Instance]`.

### 7.2 Visual query builder

- A tree that mirrors the expression: group nodes ("ALL of" / "ANY of") and term rows
  (tier combo + label combo + NOT checkbox); buttons *add term*, *add group*, *delete*;
  drag to re-order or re-nest.
- The equivalent readable expression is displayed live underneath
  (`point AND (nod OR shake) AND NOT overlap, within 2000 ms measured at beginning`).
- Validation with inline messages (only-NOT query, empty group); a query tier that is missing
  in at least one file of the corpus blocks execution with an error naming the file(s) — the
  same rule as in View 2 (§6.2).
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
  instances: instances per file, Σ total, mean per file across the corpus, sample σ (files
  of the corpus without any instance count as 0); a **breakdown by matched label
  combination** (e.g. point+nod vs point+shake) is available behind a checkbox, disabled by
  default; CSV export.
- The selection is kept as a plain `list[Instance]`, so later steps (query chaining, further
  exports) can build on it.

## 8. View 4 — Interval inspection

Fourth tab ("Interval"). For one corpus and one dictionary tier, all labels whose annotations
lie within a fixed time interval are collected; the table lists the count of every dictionary
label per file plus the dictionary coverage restricted to the interval.

- **Interval bounds:** a From/To pair, each adjustable by a **slider** *and* an exact
  **millisecond spin box**, kept in sync. The slider range follows the longest annotation
  end time of the corpus. `From ≤ To` is enforced by pushing the other bound along.
- **Containment mode:** *contained* (the annotation lies fully inside the bounds,
  boundary-inclusive — the default reading of "within") or *overlapping* (the annotation
  intersects the interval; merely touching an edge does not count).
- **Table:** one row per file; one count column per dictionary label, plus an
  out-of-dictionary column and a coverage column (`covered/|D|` as in §6.2, but only labels
  annotated **within the interval** count as covered). Mean coverage underneath; CSV export.
- The missing-tier hard error and the case-sensitivity toggle of §6.2 apply unchanged.
- Recomputed live while sliding (per-file parses are cached, so this stays instant).

## 9. View 5 — Transition matrices

Fifth tab ("Transitions"). All modes share the cell definition: cell *(i, j)* = (# times
element *i* immediately follows element *j*) / (# of all instances of element *i*). Rows are
*i* (the following element), columns are *j* (the predecessor); the row header shows the
denominator `n=…`. A row can sum to less than 1 because the first element of a sequence has
no predecessor; the ratio is undefined ("—") when element *i* never occurs.

Three **modes** (combo box):

1. **Merged sequence** — for a selectable *set* of dictionary tiers, the annotations are
   merged into one sequence ordered by start time; elements are the labels of the **union of
   the selected tiers' dictionaries**, so transitions across tiers are counted.
2. **Tier → tier** — a *source* tier (columns *j*) and a *target* tier (rows *i*) are
   selected; for every source annotation the transition target is the **next annotation on
   the target tier** (the first one with a strictly later start time). Rows can sum to more
   than 1 because several source annotations may share the same next target annotation.
3. **Compound → compound** — two compounds A and B are defined with the visual AND/OR/NOT
   builder of View 3 (same max-distance/reference-point/chain/NOT semantics, §7.1); their
   instances, anchored at their start times, form the sequence and the matrix is computed
   over {A, B} ("transition probabilities from one compound to a different compound").
   Computed on demand via a *Run compounds* button.
   **Free variables:** a compound containing `ALL <tier>` terms (§7.1) is expanded by its
   bindings — every instance becomes a matrix element named `A[label]` with the label(s) the
   free term matched, so e.g. `A = point AND ALL Head` yields separate rows/columns per
   co-occurring head label.

Common to all modes:

- **Scope:** a combo box switches between *whole corpus* — transition counts and totals are
  summed over the files before normalising; sequences never continue across a file
  boundary — and any single file of the corpus.
- Out-of-dictionary values, empty values and untimed annotations are skipped transparently
  (the surrounding annotations become adjacent). The missing-tier hard error applies; the
  case-sensitivity toggle of §6.2 applies to modes 1–2 (compounds always match
  case-sensitively, as in View 3). A "raw counts" toggle shows the numerators instead of the
  ratios. CSV export.

## 10. Testing strategy

- Hand-crafted fixture `.eaf` files in `tests/data/`: minimal file with 2 CV tiers + 1 free
  tier; file with EAF 2.8 `CV_ENTRY_ML`; file with out-of-dictionary values; file missing a
  tier; an invalid XML file; a file with precisely timed annotations across several tiers for
  the distance/co-occurrence cases.
- Unit tests for parsing (tiers/CV extraction), corpus operations (move/duplicate rules,
  JSON round-trip), and every metric in §6.2 against hand-computed values.
- Query engine tests (§7.1): AND/OR/NOT, nesting, every reference-point mode
  (beginning/midpoint/end), distance boundary cases (distance exactly `D`; the 5 ms vs
  1000 ms example from §7.1), the near-any-member NOT examples from §7.1, the chain rule
  for three positive terms (gaps 900/900 selected, 900/1100 rejected at `D` = 1000),
  interval relations, both counting modes (one-per-anchor / every combination), multi-file
  concatenation — all against hand-computed instance lists.
- Error paths: a tier missing in one file of the corpus blocks analysis and query with the
  expected message naming the file; invalid `.eaf` files are rejected on import.
- Interval tests (§8): containment boundary inclusivity, overlapping vs contained vs merely
  touching, interval-restricted coverage, corpus time extent, bound ordering in the UI.
- Transition tests (§9): per-label totals and ratios against hand-computed values, corpus
  aggregation without cross-file transitions, cross-tier union and merging,
  out-of-dictionary skipping, single-file scope, case folding, missing-tier error; tier→tier
  mode (next-target lookup, shared targets, simultaneous starts excluded); compound mode
  (plain and AND compounds, scope, validation).
- UI smoke test (optional, `pytest-qt`): build main window, simulate a drop, switch views.

## 11. Milestones

| # | Deliverable | Contents |
|---|---|---|
| 1 | Scaffolding | `pyproject.toml` (PySide6, pympi-ling; dev: pytest, ruff), package skeleton, fixtures |
| 2 | Core: ELAN parsing | `ElanDocument` + CV extraction + tests |
| 3 | Core: corpora + analysis | `CorpusProject`, all §6.2 metrics + tests |
| 4 | UI shell | `MainWindow`, tab per view, status bar |
| 5 | View 1 | filesystem panel, corpora tree, full drag & drop, corpus management |
| 6 | View 2 | selection form, counts table + Σ/mean/σ, coverage table + mean coverage |
| 7 | Core: query engine | query AST (AND/OR/NOT, max distance + reference point, interval relations), evaluator with both counting modes, near-anchor NOT, `Instance` + tests |
| 8 | View 3 | visual query builder, instance browser with tier timeline, statistics hand-off incl. combination breakdown |
| 9 | View 4 | interval label counts with slidable/enterable bounds, interval-restricted coverage |
| 10 | View 5 | transition matrices in three modes (merged sequence, tier→tier, compound→compound), raw-count toggle |
| 11 | Polish | project save/load (incl. saved queries), CSV export, background parsing/caching, error reporting |
| 12 | (Stretch) | Ctrl+drag copy, multi-label selection, bar chart (matplotlib), "open in ELAN", PyInstaller build (one-folder mode, see §3.1) |

Milestones 2–3, 7 and the core parts of 9–10 are pure-Python and reviewable independently of
any UI work.

## 12. Resolved decisions

All former open questions were decided with the project owner (2026-06-11):

1. **Coverage** is reported per *(file, tier)* with mean coverage per tier across files only
   — no additional per-file aggregate across tiers.
2. **Out-of-dictionary values** are reported in their own column and excluded from counts and
   coverage (§6.2).
3. **Label matching** is case-sensitive by default, with a UI toggle for case-insensitive
   matching (§6.2).
4. **Missing tier = hard error:** an analysis or query for tier `T` runs only if `T` exists
   in every file of the corpus; the error names the offending file(s). A present tier without
   the searched label simply yields count 0 (§6.2).
5. **UI language:** English only.
6. **Distance reference point** is one global choice per query (beginning/midpoint/end);
   AND-groups can alternatively use Allen-style interval relations (§7.1).
7. **Instance counting:** one instance per anchor annotation by default; a UI toggle switches
   to every-satisfying-combination semantics (§7.1).
8. **NOT** follows the near-any-member rule (revised after implementation review,
   superseding the earlier near-the-anchor choice): an instance is rejected iff a matching
   negated annotation lies within the max distance of **at least one** positive annotation
   of the tuple (§7.1, with examples). The positive tuple itself forms a **chain** —
   consecutive members ≤ `D` apart; `A —900— B —900— C` is selected at `D` = 1000 although
   A and C are 1800 ms apart (also revised, superseding pairwise).
9. **Instance statistics:** per-file counts, Σ total, mean across the corpus and sample σ;
   breakdown by matched label combination behind a checkbox, disabled by default (§7.4).
