# AGENTS.md — context for AI assistants and new contributors

This file captures the decisions and hard-won hardware knowledge behind this
codebase. Read it before
making changes. Task list lives in the
[GitHub issues](https://github.com/danhickstein/keckogeco/issues).

## What this project is

Control system for the laser frequency comb (LFC) at W. M. Keck Observatory.
It is a ground-up rewrite of the original Caltech 
[KeckLFC](https://github.com/kester2015/KeckLFC) code;
that old repo is a **read-only reference** — port logic from it, never its
style (no hardcoded addresses, no prints, no connect/act/disconnect churn).

Deployment target: the comb's Windows laptop (**LAPTOP-LFC2**) physically
connected to ~20 rack instruments. We have remote acccess to the laptop
and can power cycle insruments using the eaton PDUs. Physical access is 
rare. Admin access to the computer required Keck IT admin staff, which 
comes with a delay.

## Architecture

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
- **stdlib `logging`**, not loguru.
- Everything runs via `python -m keckogeco.<...>`,
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
- **Secrets**: `config/keckogeco.toml`, and
  `config/site-info.txt` are git-ignored and must never be committed. 
  sensitive info goes here.
- **IM bias locking is deliberately MANUAL (2026-07-15).** No autolock
  anywhere — an earlier saved-lockpoint auto-engage design (and the
  ported `LFC_IM_AUTO_LOCK` sweep before it) was built and then removed
  the same day; do not resurrect either. The server locks at
  operator-entered values only: photodiode setpoint + PI gains via
  `PUT /im`, starting bias via `LFC_IM_BIAS`, engage via
  `LFC_IM_LOCK_MODE` (engaging copies the manual bias into the SIM960
  output offset for a bumpless start). The GUI scan panel only
  *suggests* values (text); the operator types them into the servo
  panel. `LFC_IM_AUTO_LOCK` is unbound (retirement proposed in
  `ktl/keyword-changes.md`); the minicomb transition no longer locks.
  A reference calibration scan can be overlaid in the GUI (CSV +
  gui.toml pref, same pattern as the OSA reference spectrum). All IM
  bias adjustments are limited to ±8 V (under the SIM960's ±10 V spec).
  Beware: the transfer curve's amplitude scales with EDFA27 power, and
  at the commissioned 450 mW the lock photodetector clips above ~5.5 V
  (docs/hardware/design.md) — scan at the power you'll operate at.

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
  power-up.** That is a real state, not a protocol error: return NaN, never
  raise. Only a set can home the unit, so the driver pins the not-homed
  state (no repeated hardware reads, one debug-level hint) until an
  attenuation is set — the VOAs are unused on the rack; NaN in status
  output is the signal, the log stays quiet. **Which VOA is which wavelength
  is unknown**, so config keys are unit-serial-based (`voa_303699`, ...);
  renaming a block to `voa1310`/`voa1550`/`voa2000` once a unit is identified
  on-site is what binds its `LFC_VOAxxxx_ATTEN` keyword (`comb/controller.py`).
- **`python -m keckogeco.discovery` rewrites `[devices.*]` blocks**; it must
  pass through curated option keys (`mode`, `channel`, `baud_rate`, `note`,
  ...) and `enabled = false` from the existing config — a 2026-07-12 run
  silently dropped the EDFA `mode` and the RF oscillator PSU's `channel = 2`
  before `save_config()` learned to preserve them. Since 2026-07-13 it is
  also **non-destructive**: silent devices are kept and tagged
  `missing_since` (removal is explicit via `--prune`), `enabled = false`
  blocks are adapter-checked but never probed and never removed, and the
  pre-run file is saved as `keckogeco.toml.bak`. Deliberately ONE config
  file — a split "curated vs discovered" file pair was considered
  (2026-07-13) and rejected: a device's identity would straddle both files
  and the merge rules cost more than they protect.
- **TC-720 TECs sit on COM13 (sn AG0JO6EHA) and COM16 (sn AQ00VDL3A),
  deliberately powered off** until on-site; their blocks are
  `enabled = false`. Which is PPLN vs waveguide is unknown — renaming to
  `tec_ppln`/`tec_wvg` binds the keywords (same pattern as the VOAs).
  **COM5 (Prolific, no USB serial) is suspected to be the Rio ORION's
  adapter** — the laser itself is at OctaveHQ for Rb-cell testing; the
  `rio` block is `enabled = false` until it returns and is confirmed.
- **The USB-2408 DAQs are bound by USB serial, never via InstaCal.** The
  laptop has no InstaCal CB.CFG (probed 2026-07-13) and never needs one:
  `drivers/usb2408.py` uses `ul.ignore_instacal()` +
  `ul.create_daq_device()`. Serial **205F843 is the rack board** (`daq`;
  its ch7 reads "open connection", matching the documented Unconnected
  position) and **205F82F is the optical-table board** (`daq_eocb`).
  Discovery enumerates MCC boards passively (`mcc_inventory()`) and
  creates/verifies the `[devices.daq*]` blocks via `MCC_SERIAL_KEYS`; a
  missing board is reported but its block is never dropped. (A 2026-07-13
  run deleted the blocks because `load_existing()` gave the bare-serial
  address a synthetic `"port"` and the COM verify path discarded it —
  only COM/ASRL addresses get a `"port"` now.)
  An open/unconnected thermocouple (UL Error 145) is a real state, not a
  link fault: the driver returns NaN without tripping reconnect-once
  (rack ch7 is permanently unconnected; reconnecting on it every poll
  would be the Amonics storm all over again).
  **`ul.ignore_instacal()` may only run once per process**
  (`usb2408.ignore_instacal_once()`): a second call resets the UL device
  table and unbinds every created board, so the old per-open call made
  each board's open/reconnect break the *other* board — alternating
  "Error 1: Invalid board number" on every poll (seen live 2026-07-13).
  `mcc_inventory()` goes through the same guard.
- **The IM bias servo is SIM900 slot 3; slot 5 is the Rb lock servo**
  (old code `__LFC_IM_LOCK_connect`/`__LFC_RB_LOCK_connect` — an earlier
  `im_slot = 5` default here pointed at the Rb servo). The minicomb
  photodiode is wired straight into that SIM960's measure input (coax
  interface panel A4, "Minicomb photodiode → SRS PID"), so bias scans read
  power via `MMON` — **no DAQ involved**; the USB-2408s only read
  thermocouples. Slot number lives in the `srs` block's `im_slot` option.
- **The SIM900 host interface is RS-232 (COM23), not GPIB, since 2026-07-21.**
  A rear-panel DIP switch selects the host interface and is read **only at
  power-up**; the rightmost 5 switches double as GPIB address / RS-232 baud
  select (set to 115.2k — the config `baud_rate` must match, because a
  Device Clear reverts the unit to the DIP rate). Plug into the DB-9 female
  labeled **COMPUTER**, not the identical EAVESDROP jack next to it. Over
  RS-232 a serial `<break>` IS the Device Clear the driver uses to escape a
  CONN'd slot (`SerialTransport(break_on_clear=True)`); rack-verified: the
  driver, `module_inventory`, and slot I/O all work unchanged. The SIM960s
  keep settings through a mainframe power cycle — slot 3 came back with the
  IM lock still engaged and holding. GPIB now carries only the OSA.
- **The Pritel refuses `FA ON` while its STORED power-amp setpoint is too
  high for the measured seed** (rack-probed 2026-07-18: 3.9 A stored →
  refused, ≤ 1.0 A → fine, at the commissioned seed; the ASD reply is
  "PowerAmp pump current is disabled"). `FA PWRAMP?` reads the ACTUAL
  current (0 while off), never the stored value, so the trap is
  invisible — an abnormal shutdown (ASD trip, crash) leaves the last
  operating current stored and every later pump-on fails with all
  monitors healthy. `set_pump(True)` therefore sends `FA SETPWR 000`
  before `FA ON`; do not remove it (the sim models the refusal).
  **The pump can also come back ON with no `FA ON` at all** — observed
  2026-07-18: minutes after a confirmed `FA OFF`, following an Arduino
  interlock latch reset, the pump was live again with nothing in the
  log. So `set_pump(False)` zeroes the stored setpoint too: whatever
  re-enables the pump must find 0 A. Treat "Reset latch" as a possible
  pump-enable when the front-panel/pump state allows it.
- **The Arduino interlock (Uno, COM4) auto-resets on any port open that
  asserts DTR** (DTR is capacitor-coupled to the MCU reset pin). The
  firmware boots latched-tripped — cutting the Pritel pump in hardware —
  with thresholds reverted to compiled defaults (317/690) and the YJ
  shutter dropped to "passing". This is why killing/restarting the server
  used to trip the interlock. `SerialTransport` grew `dtr`/`rts` options
  and the driver holds both de-asserted (rack-verified 2026-07-20: latch,
  thresholds, and reply latency all survive reopens; boot-tripped on real
  power loss is preserved — that fail-safe is deliberate firmware design).
  `discovery.py` still probes COM ports with DTR asserted, so a discovery
  run WILL reboot the board and trip the latch: reset it afterward, and
  zero the Pritel stored setpoint first (a latch reset can re-enable the
  pump; see above).
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
