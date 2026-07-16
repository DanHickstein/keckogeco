# KTL keyword change list (for review with Keck)

Baseline: the 77 keywords in the old repo's `KTL server/LFC.xml.sin` (the
set previously agreed with Keck). This file tracks every deviation the
rewrite makes, for discussion before the dispatcher is redeployed.

## Corrections (schema metadata fixed to match the deployed behavior)

| Keyword | Was | Now | Why |
|---|---|---|---|
| `LFC_PTAMP_PRE_P` | "pre-amp output power", mW, 50–140 | preamp **current**, mA, 0–600 | The deployed implementation always read/wrote the Pritel preamp current in mA (`KeckLFC.py` wrote `preAmp = '{value}mA'`; standby/full-comb sequences use 0 and 600). The CSV metadata never matched. |
| `LFC_RFOSCI_I` | units mA, min 0.35, max 0.7 | units **A** (limits unchanged) | Implementation returned the GPD supply current in amps; the 0.35–0.7 limits only make sense in A. |
| `LFC_EDFA27_INPUT_POWER_MONITOR`, `LFC_EDFA23_INPUT_POWER_MONITOR` | type boolean | type **double**, mW | The deployed handlers always returned the Amonics seed input power in mW; "boolean" in the CSV was never true. |
| `LFC_IM_BIAS` | V, −3 to +3 | V, **−8 to +8** | The ±3 V bound was the commissioned auto-lock sweep range, not a hardware limit. The SIM960 output spec is ±10 V; ±8 V is the operating limit chosen (Dan, 2026-07-15) to stay under it. The servo's hardware output limits (ULIM/LLIM) and the bias-scan/auto-lock bounds are set to ±8 V to match. |
| `LFC_EDFA23_P` | "output power", mW, 0–20 | pump **current**, mA, 0–1500 | The 23 dBm unit runs in ACC, so the value written to the Amonics `:DRIV:ACC:CUR` register is a current in mA (commissioned operating point 80 mA — impossible to express under the old 0–20 bound). The driver additionally clamps to the unit's own reported maximum. `LFC_EDFA13_P` almost certainly has the same problem (that unit is also configured ACC) but is left untouched until the out-of-use 13 dBm EDFA is confirmed on the rack. |

## Additions (new keywords, not in the 77-keyword baseline)

| Keyword | Type | Why |
|---|---|---|
| `LFC_WSP_TOD` | double, writable, ps/nm² | 3rd-order dispersion companion to `LFC_WSP_PHASE`. The engineering GUI drives the WaveShaper as two numbers (GDD + TOD) instead of a full profile; the driver's `set_dispersion` always supported d3 but no keyword exposed it. `LFC_WSP_PHASE` gains units `ps/nm` and a help string naming it GDD (semantics unchanged: it was always d2). Both keywords read back the value currently applied. |
| `LFC_WSP_CENTER` | double, writable, nm (1500–1600) | Center wavelength of the GDD/TOD phase profile (defaults to the commissioned 1559.8 nm; the old orchestration hardcoded it). Applied together with `LFC_WSP_PHASE`/`LFC_WSP_TOD`. |
| `LFC_EDFA27/23/13_OUTPUT_POWER_MONITOR` | double, RO, mW | Amonics `:SENS:POW:OUT` was always readable but had no keyword; the engineering GUI displays it per EDFA. |
| `LFC_EDFA13_INPUT_POWER_MONITOR` | double, RO, mW | The 27/23 input monitors existed in the baseline; the 13 dBm unit was simply missing. |
| `LFC_PTAMP_IN` | double, RO, mW | Pritel seed input power (`FA INPUT?`), the natural companion to `LFC_PTAMP_OUT`. |
| `LFC_PTAMP_INTERLOCK_V` | double, RO, V (0–5) | The Arduino interlock's photodiode voltage (10-bit ADC scaled to volts) — the quantity `LFC_PTAMP_LATCH` judges against its thresholds, useful for seeing how much margin the interlock has. |

## Semantic notes (unchanged, but worth discussing)

- `LFC_IM_AUTO_LOCK` is **unbound — proposed for retirement**
  (2026-07-15). IM bias locking is deliberately manual: the operator
  enters the photodiode setpoint, starting bias, and PI gains in the
  engineering GUI (the GUI's bias scan suggests values) and engages via
  `LFC_IM_LOCK_MODE` — whose write-1 now also copies the manual bias
  into the SIM960 output offset so the PID takes over bumplessly. The
  minicomb/STANDBY transitions no longer engage the lock as their last
  step. Reads/writes of `LFC_IM_AUTO_LOCK` answer "not implemented"
  (501) like other unbound keywords.
- `LFC_CHECK_STATUS` still reports the legacy prime-product code
  (30030 = FULL COMB, 15015 = STANDBY, 1 = OFF). Proposal: add an
  enumerated `LFC_STATE` (OFF/STANDBY/FULL_COMB/FAULT/UNKNOWN) and keep
  `LFC_CHECK_STATUS` for compatibility.
- `LFC_SET_STANDBY` / `LFC_SET_FULL_COMB` / `LFC_SET_OFF` /
  `LFC_MINICOMB_AUTO_SETUP` writes now **enqueue** the sequence and return
  immediately (the old ICE call blocked for its full duration). Progress is
  visible via the REST `/actions/current` endpoint; a progress-string
  keyword could be added if Keck wants it in KTL.
- `ICECLK` / `ICESTA` names are kept even though ICE is replaced by HTTP,
  so existing operator lore (`show -s comb icesta`) keeps working.
  Candidates to retire once the HTTP dispatcher is deployed: `ICESTA2`,
  `ICETEST`, `TEST*`.
- `LFC_YJ_SHUTTER` vs `LFC_YJ_SHUT`: apparent duplicates in the baseline;
  the old `LFC_YJ_SHUT` handler was already a stub (its shutter call was
  commented out). The rewrite answers reads with 0 and logs-and-ignores
  writes, purely for compatibility. Propose retiring `LFC_YJ_SHUT`.
- `LFC_TEMP_MONITOR` / `LFC_RFOSCI_MONITOR` / `LFC_RFAMP_MONITOR` now
  read **True = within range** (temperatures below 40 C; RF supplies at
  their commissioned 15 V/~0.4 A and 30 V/~4.2 A envelopes, or off). The
  old handlers returned 0 normally and 1 *after* executing `CLOSE_ALL`
  and sending an email; like the rep-rate check, out-of-range now logs an
  error without auto-shutdown (see the safety note below).
- `*_DEFAULT` / `*_AUTO_ON` presets: write 1 to push the commissioned
  setpoints (EDFA27 APC 450 mW, RF amp 30 V/4.2 A, RF osc 15 V/3 A,
  Pritel 600 mA/3.9 A); `AUTO_ON` variants also enable emission. Reads
  return False (the old handlers returned nothing). Presets only ever
  set values — nothing is applied automatically at startup, and the
  Pritel power amp in particular is only raised by an explicit action
  or keyword write (driver-level current ramping applies everywhere).
- **EDFA23 parked at 0 mA (out of service, 2026-07):** the 23 dB EDFA
  is currently not used in the light path, so `LFC_EDFA23_P_DEFAULT` /
  `LFC_EDFA23_AUTO_ON` and the minicomb sequence set ACC **0 mA**
  instead of the commissioned 80 mA, with the 1-10 mW seed gate
  suspended (meaningless at zero drive). The sequence still activates
  the channel so the prime-product state code can reach FULL COMB.
  Restore the 80 mA setpoint and the seed gate when the unit returns
  to service (`comb/actions.py` and the presets in
  `comb/controller.py`).
- `SHOW_ALL_VAL` writes dump the keyword snapshot to the server log
  instead of stdout.

## Not yet implemented (remaining drivers or design decisions)

`LFC_TEMP_TEST2` and `LFC_T_EOCB_IN/OUT` bind automatically once the
second DAQ board (`daq_eocb`) is configured; `LFC_VOA1310/2000_ATTEN`
once those VOA ports are confirmed by discovery. Everything else is
bound.

Note: the Rb-lock automation from the old system is **not planned** for
the rewrite (per the LFC team, its implementation is undecided).

Safety-behavior change: the old `LFC_CHECK_STATUS` executed `CLOSE_ALL`
(full comb shutdown) from inside the status read if the Pendulum counter
was more than 1 kHz off 16 GHz. The rewrite reports/logs the fault
instead of auto-shutting down; whether an automatic response belongs in a
dedicated safety monitor is an open discussion item.
