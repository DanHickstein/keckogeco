# Development

## Setup

```bash
git clone https://github.com/danhickstein/keckogeco
cd keckogeco
python -m venv .venv && source .venv/bin/activate   # or .venv\Scripts\activate on Windows
pip install -e ".[gui,dev,docs]"
pre-commit install
```

## Running tests

```bash
pytest
ruff check .
```

Everything in the test suite runs without hardware: drivers are exercised
against `SimTransport`, which replays canned responses and records the
commands a driver sends.

## Simulation mode

`keckogeco-server --sim` builds every configured device with a
`SimTransport`, so the full server + GUI stack runs on any machine. Sim mode
returns canned values only; it exists to exercise plumbing and GUI layout,
not to model the comb.

## Building the docs

```bash
pip install -e ".[docs]"
sphinx-build -M html docs docs/_build
open docs/_build/html/index.html
```

Docs are published to GitHub Pages by CI on every push to `main`; a PDF is
built with `sphinx-build -M latexpdf` on releases.

## Design rules for drivers

- Constructors take a `Transport`; **no addresses or COM ports in code** —
  they live in the git-ignored config file.
- Persistent connections; the base class reconnects once on I/O failure,
  then raises `ConnectionLost`.
- `logging.getLogger(__name__)` only — no `print()` (enforced by ruff `T20`).
- No bare `except:` (enforced by ruff `BLE`).
- NumPy-style docstrings.
- No matplotlib or GUI imports inside drivers.
- Each driver defines a small `SIM_RESPONSES` table so it works in sim mode.
