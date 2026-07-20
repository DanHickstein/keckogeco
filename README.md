# keckogeco
Greetings! You've arrived at the software repository for the 
**Keck Observatory GHz Electro-Optic Comb (KECKOGECO)**, situated
at the lonely crossroads of astronomy, precision metrology, and nanophotonics --
where the search for a distant Earth meets the GPIB bus.

Read the documentation: <https://danhickstein.github.io/keckogeco/> (danhickstein.github.io/keckogeco)

`keckogeco` runs on the comb's Windows control laptop, where it controls the
~20 rack instruments (amplifiers, lasers, waveshapers, TEC controllers,
power supplies, diagnostics) and exposes them three ways:

- an **Instrument Server HTTP/REST API** (FastAPI) that owns the instruments, logs data, and provides an interface through http,
- an **Engineering GUI (PyQt)** with full control of every subsystem,
- a **KTL keyword service** (`comb`) via a DFW
  dispatcher running on the observatory's Linux hosts (see `ktl/`).


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

## Contributing / project status

If you have a suggestion or question, please open a new
[GitHub issue](https://github.com/danhickstein/keckogeco/issues).

[AGENTS.md](AGENTS.md) records architecture decisions and the
hardware behaviors learned on the real rack — read it before changing
drivers or the KTL keyword surface (AI coding assistants load it
automatically).

## Credits

This package is adapted from the original Caltech
[KeckLFC](https://github.com/kester2015/KeckLFC) control code, which was
developed by Maodong Gao, Jinhao Ge, Yoo Jung Kim, and Steph Leifer.


## License

MIT — see [LICENSE](LICENSE).
