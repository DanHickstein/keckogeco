# AGENTS.md — context for AI assistants and new contributors

This file captures the decisions and hard-won hardware knowledge behind this
codebase — the things you cannot re-derive by reading the code. Read it before
making changes. Task list lives in the
[GitHub issues](https://github.com/danhickstein/keckogeco/issues).

## What this project is

Control system for the laser frequency comb (LFC) at W. M. Keck Observatory,
maintained by Dan Hickstein. It is a ground-up rewrite of
the original Caltech [KeckLFC](https://github.com/kester2015/KeckLFC) code;
that old repo is a **read-only reference** — port logic from it, never its
style (no hardcoded addresses, no prints, no connect/act/disconnect churn).

Deployment target: the comb's Windows laptop (**LAPTOP-LFC2**) physically
connected to ~20 rack instruments. Dan is on-site at Keck around
**2026-07-18**; until then, work is tested on the real rack via remote
sessions with Dan pasting output back.

## Settled architecture — do not relitigate

- **One process owns the hardware**: the FastAPI server
  (`keckogeco/server/app.py`) on the laptop. Everything else (PyQt GUI, web
  page, Keck's KTL dispatcher) is a pure REST client of `/api/v1/...`.
  The GUI must never open COM ports directly.
- **KTL keyword surface is a compatibility contract.** The 77 keywords in
  `keckogeco/comb/schema/keywords.toml` come from the old
  `KTL server/LFC.xml.sin`. Any rename, retype, unit fix, or semantic change
  MUST be recorded in `ktl/keyword-changes.md` for review with Keck — never
  change one silently. The legacy prime-product status code (30030 = FULL
  COMB, 15015 = STANDBY, 1 = OFF) is kept deliberately.
- **ICE → HTTP**: the Keck Linux side keeps its DFW `combd` dispatcher but the
  ICE transport is replaced by HTTP calls to this server, using **stdlib
  `urllib` only** (kroot Python may lack `requests`). Phase 3, not yet built.
- **stdlib `logging`**, not loguru. **Simulation is deliberately minimal**
  (`SimTransport` canned responses) — do not build a physics model.
- **No console-script exes.** Everything runs via `python -m keckogeco.<...>`,
  and the four entry scripts (`discovery.py`, `check.py`, `server/app.py`,
  `gui/app.py`) also run as bare files via the `if __package__ in (None, "")`
  bootstrap header at their top — keep those headers and their absolute
  imports intact.
- Transitions (`comb/actions.py`) were **ported verbatim** from the old
  system's commissioning-tested sequences (ramp rates, gate thresholds,
  step order). Do not "optimize" ramp parameters or reorder steps without
  Dan's sign-off.

## Explicit directives from Dan

- **No email/SMTP alert notifications.** Alerts go to the log only.
- **Do NOT implement the Rb lock** (`rb_lock`) — undecided whether it will
  ever be used. The Pendulum counter IS wanted and is ported.
- **Secrets**: `config/keckogeco.toml` and `config/secrets.toml` are
  git-ignored and must never be committed. Eaton PDU credentials go through
  Windows Credential Manager (`keyring`); the old public repo leaked them
  (user `lfc` / password `lfc@keck`), and they will be changed on-device
  on-site — never write credentials into the tree, docs, or examples.

## Environments

- **Dev (Mac/Linux/CI)**: normal venv, `pip install -e ".[gui,dev,docs]"`,
  `pytest` (all tests run without hardware), `ruff check .`.
- **The laptop (LAPTOP-LFC2)**: Windows, **system Python 3.13, user-site pip
  install, no venv, no admin rights**. Repo at `C:\kecklfc\keckogeco`. The
  user-site Scripts dir is off PATH — another reason everything is
  `python -m`. Dan runs scripts with VSCode's Run button.
- **wsapi** (Finisar WaveShaper DLL API): pip can't install it in place under
  `Program Files` without admin — copy
  `...\Finisar\WaveManager\waveshaper\api\python3` to a writable dir and
  `pip install` from there. The driver (`drivers/waveshaper.py`) registers
  the WaveManager `bin\amd64` DLL directory via `os.add_dll_directory`
  before `import wsapi`, so no System32 copies are needed.

## Hardware gotchas learned on the real rack — do not "simplify" these away

- **Amonics PM-13/PM-23 EDFAs (edfa13/edfa23) drop the first command after
  the serial port opens.** `amonics_edfa._configure()` re-sends
  `:CAL:SYS:MODEL?` up to 3× **on the same open port**. Do not replace this
  with the base class's close-reopen-retry — reopening makes the retry a
  "first command" again and the unit never answers (that was the original
  25 s-timeout bug).
- **Amonics unsupported queries get no reply at all** (silent VISA timeout,
  never an error string). Probed on the rack 2026-07-12: the PM-13/PM-23
  never answer `:MODE:SW:CHn?` (the PM-27 does), and every unit ignores
  `:DRIV:<mode>:CUR/STAT` queries for the mode that is *not* active. The
  control mode is fixed in config (`mode = "ACC"/"APC"` per device block;
  edfa27 runs APC, edfa13/23 ACC) and cached in the driver — re-querying it
  through the reconnect-once path cost a ~10 s reconnect storm per unit on
  every poll cycle.
- **The RF oscillator PSU is Instek GPD-4303S channel 2, not 1** (15 V / 3 A).
  The RF amp is the GPP-1326 channel 1 (30 V / 4.2 A). Channel numbers come
  from the `channel` option in the device config, read via `psu_channel()`.
- **OZ Optics VOAs answer `Atten:unknown` until their first move after
  power-up.** That is a real state, not a protocol error: return NaN with a
  logged hint (already implemented), never raise.
- **hk_shutter is on COM8; the Agiltron 2×2 switch is on COM12.** The old
  code's hardcoded values had these swapped. Never trust old hardcoded
  ports — discovery anchors devices by USB adapter serial instead.
- **VISA lists USB-TMC resources under two spellings**
  (`...::0::INSTR` and `...::INSTR`). Discovery dedupes via
  `normalize_visa_addr()` — which must **never rewrite GPIB addresses**.
- **TC-720 TECs use standard two's complement** for negative values; the
  vendor doc's described encoding doesn't round-trip and is wrong.
- **Rep-rate safety behavior changed deliberately**: a bad Pendulum reading
  (16 GHz ± 1 kHz expected on channel C when the RF chain is up) logs an
  error; it no longer triggers the old automatic CLOSE_ALL. Recorded in
  `ktl/keyword-changes.md`.
- **Keyword schema corrections** (also in keyword-changes.md):
  `LFC_PTAMP_PRE_P` is mA 0–600 (old schema said mW 50–140);
  `LFC_RFOSCI_I` is amps, not mA.
- The old repo's addresses/units/limits are frequently wrong; when the rack
  disagrees with the old code, the rack wins, and the discrepancy gets a
  line in `ktl/keyword-changes.md` if it touches a keyword.

## Code conventions

See `docs/development.md` for the driver design rules (Transport injection,
no addresses in code, persistent connections with reconnect-once,
per-instrument `RLock`, `logging` not `print`, NumPy docstrings, a
`sim_responses()` table per driver). Every change keeps `pytest` (145 tests,
no hardware needed) and `ruff check .` green; CI runs both on ubuntu +
windows. New drivers follow the existing module pattern — read
`drivers/instek_psu.py` or `drivers/oz_voa.py` as templates, and give config
metadata keys a home in `DISCOVERY_KEYS`/`CONTROLLER_KEYS` so they don't leak
into transport kwargs.

## Status snapshot (2026-07-12)

Working end-to-end in sim: server + PyQt GUI + actions + IM auto-lock;
61/77 keywords bound. First real-rack run: 7/14 discovered devices passed
immediately (EDFA27 live at 450 mW, both Insteks, Pritel, Rb clock locked,
hk_shutter, Arduino interlock). Pending on the rack: GPIB is down until an
NI-488.2 downgrade (blocks SIM900/IM lock, Pendulum, Agilent OSA), TC-720s
silent (power?), one Keysight FG wedged. Remaining work is tracked in the
GitHub issues.
