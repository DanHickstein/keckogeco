# keckogeco

**Keck Observatory GHz Electro-Optic Comb** — control system for the laser
frequency comb (LFC) deployed at the W. M. Keck Observatory.

`keckogeco` runs on the comb's Windows control laptop, where it owns the
~20 rack instruments (amplifiers, lasers, waveshapers, TEC controllers,
power supplies, diagnostics) and exposes them three ways:

- a **PyQt engineering GUI** with full control of every subsystem,
- an **HTTP/REST API** (FastAPI) that also serves a lightweight **web status
  page**, and
- through that API, the Keck-side **KTL keyword service** (`comb`) via a DFW
  dispatcher running on the observatory's Linux hosts (see `ktl/`).

This package is a ground-up rewrite of the original Caltech
[KeckLFC](https://github.com/kester2015/KeckLFC) control code by
[Octave Photonics](https://www.octavephotonics.com).

## Install

```bash
pip install -e ".[gui,dev]"
```

Instrument addresses and site-specific settings live in a git-ignored config
file — see [config/README.md](config/README.md). To get started:

```bash
python -m keckogeco.discovery     # auto-discover instruments, write config
python -m keckogeco.check         # validate config, try connecting to each device
python -m keckogeco.server.app    # start the control server (add --sim for no hardware)
python -m keckogeco.gui.app       # engineering GUI (talks to the server over REST)
```

These four entry points also run as plain files — open `keckogeco/discovery.py`,
`keckogeco/check.py`, `keckogeco/server/app.py`, or `keckogeco/gui/app.py` in
your editor and press Run.

## Simulation mode

Everything opens without hardware for development and layout work:

```bash
python -m keckogeco.server.app --sim
python -m keckogeco.gui.app
```

Simulated instruments return canned values only — this is for exercising the
GUI/API plumbing, not for modeling comb physics.

## Documentation

Built with Sphinx and published at
<https://danhickstein.github.io/keckogeco/> (HTML) with a PDF artifact on each
release.

## Repository layout

| Path | Contents |
|---|---|
| `keckogeco/drivers/` | one module per instrument, on a shared `Instrument`/`Transport` base |
| `keckogeco/comb/` | orchestration: controller, KTL keyword registry, state machine, monitors, locking routines |
| `keckogeco/server/` | FastAPI REST server + static web status page |
| `keckogeco/gui/` | PyQt engineering GUI (pure REST client) |
| `ktl/` | Keck-side files: DFW dispatcher, keyword definitions, KTL-native GUI |
| `config/` | example configuration (the real one is git-ignored) |
| `docs/` | Sphinx documentation |

## License

MIT — see [LICENSE](LICENSE).
