# Keck-side files: the `comb` KTL service

The LFC instruments are owned by the keckogeco server on the Windows
laptop. The `comb` KTL service, running on `irastrocomb`, proxies the
keywords to that server over HTTP. This directory holds everything that
deploys through Keck's build system (not part of the pip package):

| File | Purpose |
|---|---|
| `combd.sin` | The DFW dispatcher, HTTP backend (successor to the ICE version). |
| `LFC.xml.sin` | Keyword definitions — the agreed 77-keyword bundle. |
| `combd.conf.example` | Dispatcher configuration template (server URL, token, timeout). |
| `keyword-changes.md` | Every deviation from the old system, for review with Keck. |
| `ktl_gui.py` | Minimal KTL-native operator GUI (tkinter + `ktl` only). |

## How it works

Each keyword in the LFC bundle becomes an HTTP-backed DFW keyword:
reads are served from a ≤2 s cached bulk snapshot of
`GET /api/v1/keywords` (falling back to a single-keyword fetch), and
writes `PUT /api/v1/keywords/<NAME>`. Only the Python standard library
(`urllib`) is used — no `requests`, no ICE, no site packages beyond
kroot's `DFW`/`ktl`.

The old operator semantics are preserved:

- `ICESTA`: 1 = connected, 2 = disconnected, 3 = written by the operator
  to (re)connect. The dispatcher probes `GET /api/v1/health` on connect
  and every 5 s while connected.
- `ICECLK`: heartbeat poked by the laptop server (~1 Hz); a stale value
  means the laptop side is down even if the network is up.
- Per-keyword polling: slow period 120 s (or the XML `period`
  attribute), accelerated to 3 s for a couple of cycles after a write.
- Writes rejected by the server (validation 400, busy transition 409,
  instrument failure 502) surface to the operator as the usual
  `ERR_WRITE_SW_ERROR`, with the server's detail message in the
  dispatcher log.

Operator commands are unchanged — see the
[Keck operations guide](https://danhickstein.github.io/keckogeco/user_guide/keck_operations.html).

## Deploying

The standard astrocomb build flow (unchanged from the ICE era):

```console
# 1. copy the files to the build host
scp combd.sin combbld@irastrocombbuild:/kroot/src/kss/astrocomb/comb/dispatcher/combd.sin
scp LFC.xml.sin combbld@irastrocombbuild:/kroot/src/kss/astrocomb/comb/ktlxml/LFC.xml.sin

# 2. build and install
ssh combbld@irastrocombbuild
cd /kroot/src/kss/astrocomb/comb/
make install

# 3. deploy to irastrocomb
kdeploy -a

# 4. restart the dispatcher
ssh combbld@irastrocomb
astrocomb restart comb
```

The dispatcher config (from `combd.conf.example`) needs `server_url`
pointing at the laptop's keckogeco server — the address lives in the
config, never in the source. First-time smoke test from any KTL host:

```console
$ modify -s comb icesta=3
$ show -s comb icesta iceclk lfc_edfa27_p     # live values
$ modify -s comb lfc_hk_shutter=1             # a safe write round-trip
```

## Differences from the ICE dispatcher

- No ICE, no slice files, no `server.py` on the laptop — the keckogeco
  server replaces it and runs permanently.
- `ICESTA=3` probes and connects within a second or two (no 20 s
  handshake wait).
- Transition writes (`lfc_set_*`) return immediately and the sequence
  runs server-side; other writes get rejected (409) until it finishes.
- `ICENCALL` counts HTTP requests instead of ICE calls.

Everything else — keyword names, types, the `show`/`modify` workflows,
the build/deploy procedure — is intentionally identical. Keyword-level
changes are cataloged in `keyword-changes.md`.

## Not yet done

- `combd.sin` has not run against a real kroot yet — it needs a first
  supervised deployment on `irastrocombbuild` (tracked in GitHub
  issue #7). The HTTP client half is exercised against the keckogeco
  server; the DFW half follows the old dispatcher line by line.
