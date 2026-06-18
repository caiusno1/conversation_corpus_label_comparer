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
4. **Transitions** — row-stochastic transition matrices per file or for the
   whole corpus: cell (i, j) is the probability that element i is immediately
   followed by element j (count of i→j divided by the transitions out of i), so
   every row sums to 1. Three modes: *merged sequence* (several tiers combined
   via the union of their dictionaries), *tier → tier* (rows = a source tier's
   labels, columns = the next annotation's label on a target tier), and
   *compound → compound* (two compounds defined with the AND/OR/NOT query
   builder; an `ALL <tier>` free-variable term matches any label in range and
   expands the matrix by the bound labels, e.g. `A[nod]`, `A[shake]`).
   Raw-count toggle and CSV export.
5. **Query** — build boolean queries (AND / OR / NOT, nestable) over annotation
   labels with a maximum temporal distance (measured at the beginning, midpoint,
   or end of annotations) or an Allen-style interval relation; `ALL <tier>`
   terms act as free variables matching any label in range. Step through the
   matched instances one by one in a tier timeline, choose which tiers are
   visible, then feed the selected instances into per-file / corpus statistics
   (with an optional breakdown by label combination). CSV export.

## Installation

Requires Python ≥ 3.10 (PySide6 wheels for the very newest Python release can
lag by a few months). The only runtime dependency is **PySide6 (Qt for
Python)**. Install into an **isolated environment** — a virtual environment or a
dedicated conda environment — rather than a shared/base interpreter; Qt is
sensitive to conflicting libraries from other installations, which is the usual
cause of import failures (see [Troubleshooting](#troubleshooting)).

### Option 1 — virtual environment (recommended)

Create the environment with a python.org / system Python:

```bash
# Windows (PowerShell) — the py launcher avoids picking a conda interpreter
py -3.12 -m venv .venv
.venv\Scripts\activate

# macOS / Linux
python3 -m venv .venv
source .venv/bin/activate
```

Then, inside the activated environment:

```bash
python -m pip install --upgrade pip
pip install -e .          # runtime: PySide6
pip install -e ".[dev]"   # plus pytest, pytest-qt, ruff (for development)
```

### Option 2 — conda

Use a **dedicated** environment, not `base`:

```bash
conda create -n cclc python=3.11
conda activate cclc
pip install -e .
```

If importing Qt still fails inside conda (see [Troubleshooting](#troubleshooting)),
let conda provide Qt itself instead of the PyPI wheel:

```bash
pip uninstall -y PySide6 PySide6-Essentials PySide6-Addons shiboken6
conda install -c conda-forge pyside6
pip install -e . --no-deps   # install this package without re-pulling PySide6 from PyPI
```

### Option 3 — plain pip

If you already manage your environment, a direct install works too:

```bash
pip install -e .
```

## Running

```bash
cclc            # via the installed entry point
# or
python -m cclc.main
```

Quick check that Qt loads correctly in your environment:

```bash
python -c "from PySide6 import QtWidgets; print('ok')"
```

## Standalone Windows installer

For end users who don't want to install Python at all, the app can be shipped as
a **self-contained Windows installer**. The build bundles its own private Python
interpreter and Qt, so there is nothing to install beforehand and no conflict
with any Python already on the machine (it also avoids the Anaconda DLL clash
described under [Troubleshooting](#troubleshooting)).

The installer is produced with **PyInstaller** (one-folder mode, which keeps the
Qt libraries as separate, replaceable files — required for LGPL compliance, see
[Licensing](#licensing)) wrapped in an **Inno Setup** installer. A Windows `.exe`
must be built on Windows; there are two ways to get it:

### Get it from CI (no local setup)

The [`Build Windows installer`](.github/workflows/build-windows.yml) GitHub
Actions workflow builds everything on a Windows runner. Trigger it from the
repository's **Actions** tab ("Run workflow"), or push a `v*` tag, then download
`ELAN-Corpus-Label-Comparer-Setup` from the run's artifacts (a tag also attaches
the installer to the GitHub release). Run the downloaded `…-Setup.exe` — it
installs per user (no administrator rights), adds a Start Menu entry, and
registers an uninstaller.

### Build it locally on Windows

Requires a python.org Python (the `py` launcher) and, for the installer step,
[Inno Setup](https://jrsoftware.org/isdl.php):

```powershell
# from the repository root
powershell -ExecutionPolicy Bypass -File packaging\build_windows.ps1
```

This produces:

- `dist\ELAN Corpus Label Comparer\` — a portable one-folder app (run the
  `.exe` inside directly, or copy the folder anywhere), and
- `dist-installer\ELAN-Corpus-Label-Comparer-Setup.exe` — the installer (only if
  Inno Setup is installed).

To freeze the app without the installer step, just run
`pyinstaller packaging/cclc.spec` in an environment where the project and its
`build` extra are installed (`pip install -e ".[build]"`). The same spec works on
macOS and Linux to produce a self-contained app for those platforms.

## Development

The analysis core (`src/cclc/core/`) is pure Python with no Qt dependency and is
fully unit-tested; the Qt UI (`src/cclc/ui/`) only renders and delegates.

```bash
# Core + UI tests. The UI smoke tests use Qt's offscreen platform.
QT_QPA_PLATFORM=offscreen pytest
ruff check src tests
```

## Troubleshooting

### Windows: `ImportError: DLL load failed while importing QtWidgets: The specified procedure could not be found`

When Qt is imported it loads several DLLs, including the Microsoft Visual C++
runtime (`msvcp140.dll`, `vcruntime140.dll`). Windows searches the **folder of
the running `python.exe` first**, so if that interpreter ships its *own*, older
copies of those runtime DLLs, they get loaded instead of the newer ones in
`C:\Windows\System32`. The old copies are missing symbols Qt 6 needs, which
surfaces as *"The specified procedure could not be found"* (Windows error 127).
The same can happen when another application puts old Qt or MSVC runtime DLLs
earlier on your `PATH`.

This is most common with **Anaconda / Miniconda**: the `base` environment keeps
`msvcp140.dll` / `vcruntime140.dll` right next to its `python.exe`
(e.g. `C:\Users\<you>\anaconda3\`), so a `pip install`ed PySide6 launched from
`base` picks up those stale DLLs and fails to load — even though PySide6 and the
Qt DLLs themselves are installed correctly.

**Confirm the cause** (in PowerShell use `where.exe`, not the `where` alias):

```powershell
where.exe msvcp140.dll vcruntime140.dll
```

If a copy under an Anaconda/conda folder is listed *before* the one in
`C:\Windows\System32`, that shadowing is the problem.

**Fix — run from an isolated environment** so the stale DLLs are no longer next
to your interpreter:

- **Virtual environment:** create a python.org venv as in
  [Installation Option 1](#option-1--virtual-environment-recommended). Its
  `python.exe` lives in `.venv\Scripts\`, which has no stray runtime DLLs, so Qt
  loads the correct ones from System32. Verify the interpreter:

  ```powershell
  python -c "import sys; print(sys.executable)"   # must be inside .venv, not anaconda3
  ```

- **Conda:** use a **dedicated** environment (Installation Option 2), not
  `base` — a freshly created env carries its own up-to-date runtime next to its
  `python.exe`. If it still fails, install Qt from conda-forge (the
  `conda install -c conda-forge pyside6` variant) so conda keeps Qt and its
  runtime consistent. As an in-place alternative you can refresh the base
  runtime with `conda update -n base -c conda-forge vc14_runtime`.

The check that you're clear, in any environment:

```bash
python -c "from PySide6 import QtWidgets; print('ok')"
```

### Linux: Qt fails to start, or "could not connect to display"

On headless machines (CI, servers, containers) use Qt's offscreen platform:

```bash
QT_QPA_PLATFORM=offscreen python -m cclc.main
```

Minimal Linux installs may also need a few system libraries, e.g. on
Debian/Ubuntu:

```bash
sudo apt-get install libegl1 libgl1 libxkbcommon0 libdbus-1-3
```

## Licensing

This project is released under the MIT License (see `LICENSE`).

It uses **Qt for Python (PySide6)**, which is licensed under the
**GNU Lesser General Public License v3 (LGPLv3)**. PySide6 is used unmodified as
a dynamically linked dependency, which keeps this application free to remain
MIT-licensed. Qt is a trademark of The Qt Company Ltd. The LGPLv3 text is
available at <https://www.gnu.org/licenses/lgpl-3.0.html>.
