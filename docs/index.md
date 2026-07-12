# keckogeco

Control system for the **Keck Observatory GHz Electro-Optic Comb** — the
laser frequency comb (LFC) deployed at the W. M. Keck Observatory,
maintained by [Octave Photonics](https://www.octavephotonics.com).

`keckogeco` runs on the comb's Windows control laptop, owns the ~20 rack
instruments, and exposes them through a PyQt engineering GUI, an HTTP/REST
API with a built-in web status page, and (via that API) the Keck-side KTL
keyword service `comb`.

```{toctree}
:maxdepth: 2

user_guide/index
instruments/index
keck/index
api/index
development
```

## Quick start

```bash
pip install -e ".[gui,dev]"
python -m keckogeco.server.app --sim      # start the server without hardware
python -m keckogeco.gui.app               # engineering GUI
```

See {doc}`user_guide/index` for operating procedures and
{doc}`development` for contributor setup.
