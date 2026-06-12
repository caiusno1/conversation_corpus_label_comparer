# conversation_corpus_label_comparer

A desktop application (Python + Qt) for organising ELAN annotation files
(`.eaf`) into corpora and analysing annotation counts, controlled-vocabulary
("dictionary") coverage, and temporal co-occurrence queries across them.

See [PLAN.md](PLAN.md) for the full design and the resolved design decisions.

## Features

Five views (tabs):

1. **Corpora** — pick `.eaf` files from a filesystem tree and organise them into
   named corpora by drag & drop (from the panel, from the OS file manager, and
   between corpora). Files are only *referenced* — never moved or copied on disk.
   Projects can be saved and reopened.
2. **Analysis** — for any tier that follows a dictionary, count a selected label
   per file (total, mean, sample standard deviation), and show dictionary
   coverage per file plus the mean coverage across the corpus. Out-of-dictionary
   values are reported separately. CSV export.
3. **Interval** — pick a tier and a fixed time window (bounds adjustable by
   sliders *and* exact millisecond fields, kept in sync) and see, per file, the
   count of every dictionary label annotated within that window, plus the
   dictionary coverage restricted to the interval. CSV export.
4. **Transitions** — transition matrices per file or for the whole corpus:
   cell (i, j) shows how often element i appeared immediately after element j,
   divided by all instances of element i. Three modes: *merged sequence*
   (several tiers combined via the union of their dictionaries), *tier → tier*
   (from each source-tier annotation to the next annotation on a target tier),
   and *compound → compound* (two compounds defined with the AND/OR/NOT query
   builder). Raw-count toggle and CSV export.
5. **Query** — build boolean queries (AND / OR / NOT, nestable) over annotation
   labels with a maximum temporal distance (measured at the beginning, midpoint,
   or end of annotations) or an Allen-style interval relation. Step through the
   matched instances one by one in a tier timeline, choose which tiers are
   visible, then feed the selected instances into per-file / corpus statistics
   (with an optional breakdown by label combination). CSV export.

## Installation

Requires Python ≥ 3.10.

```bash
pip install -e .          # runtime: PySide6
pip install -e ".[dev]"   # plus pytest, pytest-qt, ruff
```

## Running

```bash
cclc            # via the installed entry point
# or
python -m cclc.main
```

## Development

The analysis core (`src/cclc/core/`) is pure Python with no Qt dependency and is
fully unit-tested; the Qt UI (`src/cclc/ui/`) only renders and delegates.

```bash
# Core + UI tests. The UI smoke tests use Qt's offscreen platform.
QT_QPA_PLATFORM=offscreen pytest
ruff check src tests
```

## Licensing

This project is released under the MIT License (see `LICENSE`).

It uses **Qt for Python (PySide6)**, which is licensed under the
**GNU Lesser General Public License v3 (LGPLv3)**. PySide6 is used unmodified as
a dynamically linked dependency, which keeps this application free to remain
MIT-licensed. Qt is a trademark of The Qt Company Ltd. The LGPLv3 text is
available at <https://www.gnu.org/licenses/lgpl-3.0.html>.
