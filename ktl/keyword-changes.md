# KTL keyword change list (for review with Keck)

Baseline: the 77 keywords in the old repo's `KTL server/LFC.xml.sin` (the
set previously agreed with Keck). This file tracks every deviation the
rewrite makes, for discussion before the dispatcher is redeployed.

## Corrections (schema metadata fixed to match the deployed behavior)

| Keyword | Was | Now | Why |
|---|---|---|---|
| `LFC_PTAMP_PRE_P` | "pre-amp output power", mW, 50–140 | preamp **current**, mA, 0–600 | The deployed implementation always read/wrote the Pritel preamp current in mA (`KeckLFC.py` wrote `preAmp = '{value}mA'`; standby/full-comb sequences use 0 and 600). The CSV metadata never matched. |
| `LFC_RFOSCI_I` | units mA, min 0.35, max 0.7 | units **A** (limits unchanged) | Implementation returned the GPD supply current in amps; the 0.35–0.7 limits only make sense in A. |

## Semantic notes (unchanged, but worth discussing)

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
  only `LFC_YJ_SHUTTER` is implemented. Propose retiring `LFC_YJ_SHUT`.

## Not yet implemented (remaining drivers or design decisions)

`LFC_2BY2_SWITCH`, `LFC_CLARITY_ONOFF` (drivers not yet ported),
`LFC_IM_LOCK_MODE`, `LFC_IM_RF_ATT`, the `LFC_TEMP/RFOSCI/RFAMP_MONITOR`
toggles, `*_DEFAULT`/`*_AUTO_ON` keywords, `LFC_TEMP_TEST1/2`.

Note: the Rb-lock automation from the old system is **not planned** for
the rewrite (per Octave, its implementation is undecided).

Safety-behavior change: the old `LFC_CHECK_STATUS` executed `CLOSE_ALL`
(full comb shutdown) from inside the status read if the Pendulum counter
was more than 1 kHz off 16 GHz. The rewrite reports/logs the fault
instead of auto-shutting down; whether an automatic response belongs in a
dedicated safety monitor is an open discussion item.
