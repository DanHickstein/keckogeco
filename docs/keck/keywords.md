# KTL keyword reference

The `comb` KTL service exposes 77 keywords. The machine-readable source
of truth is the schema at `keckogeco/comb/schema/keywords.toml` (types,
units, limits, enums); this page adds the operational semantics, merged
from the original operations manual. Deviations from the old system are
tracked in `ktl/keyword-changes.md`.

**Access** column: RO = read-only; RW = writable; RW 🔒 = writable, and
the original manual designated it passcode-gated in the operator GUI
(the keyword layer itself does not enforce this — GUIs do).

## High-level commands

Writes of 1 **enqueue** the sequence and return immediately; progress is
visible via `/api/v1/actions/current` and the engineering GUI. One
sequence runs at a time; other keyword writes are rejected while it
runs.

| Keyword | Type | Access | Description |
|---|---|---|---|
| `LFC_SET_STANDBY` | integer | RW | Drive the comb to STANDBY: RF chain + seeded EDFAs up, Pritel power amp down. |
| `LFC_SET_FULL_COMB` | integer | RW | Bring the comb fully up: RF chain, EDFAs, Pritel to operating current. Takes on the order of a minute. |
| `LFC_SET_OFF` | integer | RW 🔒 | Shut the comb down in a safe, predefined order. |
| `LFC_MINICOMB_AUTO_SETUP` | enumerated | RW 🔒 | Automatic minicomb bring-up (RF chain, seeded EDFAs with input-power gates), finishing with the IM bias auto-lock. 1 = DONE, 0 = WRONG. |
| `LFC_CLOSE_ALL` | boolean | RW 🔒 | Emergency shutdown of every optical/RF output. |
| `LFC_IM_AUTO_LOCK` | boolean | RW 🔒 | Run the IM bias auto-lock: sweep the bias, pick the mid-fringe point, engage the SIM960 PID. |

## Comb status

| Keyword | Type | Access | Description |
|---|---|---|---|
| `LFC_CHECK_STATUS` | integer | RO* | Legacy prime-product health code: each healthy subsystem contributes a prime factor (Pritel 2, EDFA23 3, EDFA27 5, RF osc 7, RF amp 11, rep-rate 13). 30030 = FULL COMB, 15015 = STANDBY, 1 = OFF. Polled by the old GUI every ~30 min. |
| `LFC_CHECK_FULLCOMB` | enumerated | RO* | 1 = full comb, 0 = not. In the old system this had to be *written* with 2 and read back seconds later; it is now a plain read. |

*Schema marks these writable for compatibility; writes are ignored.

## Health monitors

All monitors now read **True/1 = within range**. In the old system a
fault triggered an automatic `LFC_CLOSE_ALL` plus email; the rewrite
logs an error instead (see the change list).

| Keyword | Type | Access | Description |
|---|---|---|---|
| `LFC_TEMP_MONITOR` | boolean | RO | Rack thermocouples all below the alarm level. Old polling ~20 s. |
| `LFC_RFOSCI_MONITOR` | boolean | RO | RF oscillator supply within its envelope (15 V; ~0.41 A steady-state, transiently up to ~0.62 A while warming). Old polling ~20 s. |
| `LFC_RFAMP_MONITOR` | boolean | RO | RF amplifier supply within its envelope (30 V; ~4.2 A driven, ~0.7 A idling). Old polling ~20 s. |
| `LFC_PENDULEM_FREQ_MONITOR` | boolean | RO | Rep-rate within 16 GHz ± 1 kHz on the Pendulum counter (only meaningful with the RF chain up). |
| `LFC_EDFA27_INPUT_POWER_MONITOR` | double, mW | RO | EDFA27 seed input power (retyped from the baseline's "boolean": the deployed handler always returned mW). Old polling ~120 s. |
| `LFC_EDFA23_INPUT_POWER_MONITOR` | double, mW | RO | EDFA23 seed input power (retyped, as above). Old polling ~120 s. |
| `LFC_EDFA13_INPUT_POWER_MONITOR` | double, mW | RO | EDFA13 seed input power. **New in the rewrite.** |
| `LFC_EDFA27/23/13_OUTPUT_POWER_MONITOR` | double, mW | RO | Amonics output power monitors (`:SENS:POW:OUT`). **New in the rewrite.** |

## Amonics EDFAs

| Keyword | Type | Access | Description |
|---|---|---|---|
| `LFC_EDFA27_P` | double, mW, 0–630 | RW 🔒 | 27 dBm EDFA output power setpoint / monitor (APC 450 mW is the commissioned operating point). |
| `LFC_EDFA27_ONOFF` | boolean | RW 🔒 | 27 dBm EDFA emission on/off. |
| `LFC_EDFA27_P_DEFAULT` | boolean | RW 🔒 | Write 1: set APC mode at 450 mW (setpoint only). |
| `LFC_EDFA27_AUTO_ON` | boolean | RW 🔒 | Write 1: APC 450 mW *and* enable emission. |
| `LFC_EDFA13_P` | double, mW, 0–20 | RW 🔒 | Small (20 mW) EDFA output power setpoint. |
| `LFC_EDFA13_ONOFF` | boolean | RW 🔒 | Small EDFA emission on/off. |
| `LFC_EDFA23_P` | double, mW, 0–20 | RW 🔒 | 23 dBm EDFA output power setpoint / monitor. |
| `LFC_EDFA23_ONOFF` | boolean | RW 🔒 | 23 dBm EDFA emission on/off. |
| `LFC_EDFA23_P_DEFAULT` | boolean | RW 🔒 | Write 1: apply the EDFA23 default. Commissioned value was ACC 80 mA; **currently parks at 0 mA** while the unit is out of service. |
| `LFC_EDFA23_AUTO_ON` | boolean | RW 🔒 | Write 1: apply the EDFA23 default and enable emission (dark at the current 0 mA park value). |

## RF chain

| Keyword | Type | Access | Description |
|---|---|---|---|
| `LFC_RFOSCI_V` | double, V (15) | RW 🔒 | RF oscillator supply voltage — fixed at 15 V. |
| `LFC_RFOSCI_I` | double, A, 0.35–0.7 | RW 🔒 | RF oscillator supply current limit. Typical draw ~0.41 A steady-state, up to ~0.62 A while warming. |
| `LFC_RFOSCI_ONOFF` | boolean | RW 🔒 | RF oscillator supply output on/off (Instek GPD channel 2). |
| `LFC_RFOSCI_DEFAULT` | boolean | RW 🔒 | Write 1: set the commissioned 15 V / 3 A limits (output state unchanged). |
| `LFC_RFAMP_V` | double, V (30) | RW 🔒 | RF amplifier supply voltage — fixed at 30 V, no ramping. |
| `LFC_RFAMP_I` | double, A | RW 🔒 | RF amplifier supply current limit (commissioned 4.2 A). |
| `LFC_RFAMP_ONOFF` | boolean | RW 🔒 | RF amplifier supply output on/off. |
| `LFC_RFAMP_DEFAULT` | boolean | RW 🔒 | Write 1: set the commissioned 30 V / 4.2 A limits. |

## Pritel amplifier

The most safety-critical subsystem: the power amp feeds the nonlinear
fiber and octave waveguide, and an Arduino latching relay interlocks it
against seed loss.

| Keyword | Type | Access | Description |
|---|---|---|---|
| `LFC_PTAMP_PRE_P` | double, mA, 0–600 | RW 🔒 | Preamp pump current (600 mA is the operating point). |
| `LFC_PTAMP_PRE_P_DEFAULT` | boolean | RW 🔒 | Write 1: preamp to 600 mA. |
| `LFC_PTAMP_I` | double, A, 0–4.2 | RW 🔒 | Power-amp pump current. Normal operation 3.8–4.2 A; the driver ramps all changes. Hardware limit is 5 A but >4.2 A risks the optics. |
| `LFC_PTAMP_I_DEFAULT` | boolean | RW 🔒 | Write 1: power amp to the commissioned current (ramped). |
| `LFC_PTAMP_OUT` | double, W, 0–4 | RO | Power-amp optical output power. |
| `LFC_PTAMP_IN` | double, mW | RO | Seed input power (`FA INPUT?`). **New in the rewrite.** |
| `LFC_PTAMP_ONOFF` | boolean | RW 🔒 | Pritel pump emission on/off. |
| `LFC_PTAMP_LATCH` | enumerated | RW 🔒 | Arduino interlock latch: 1 = ready, 0 = tripped-but-resettable, 3 = input too high, 5 = input too low, 4 = unknown. Write 1 to reset after a trip. |
| `LFC_PTAMP_INTERLOCK_V` | double, V, 0–5 | RO | Interlock photodiode voltage (Arduino 10-bit ADC scaled to volts) — the value the latch judges against its thresholds. **New in the rewrite.** |

## Intensity-modulator lock

| Keyword | Type | Access | Description |
|---|---|---|---|
| `LFC_IM_BIAS` | double, V, −3…+3 | RW 🔒 | IM bias voltage (normally owned by the SIM960 servo). |
| `LFC_IM_LOCK_MODE` | boolean | RW 🔒 | 1 = PID (locked), 0 = manual. |
| `LFC_IM_RF_ATT` | double, V, 0–10 | RW 🔒 | Voltage-controlled RF attenuator in the minicomb drive (GPD channel 3; default ≈ 0.72–0.8 V). |

## Seed and reference lasers

| Keyword | Type | Access | Description |
|---|---|---|---|
| `LFC_RIO_T` | double, °C | RW 🔒 | RIO pump laser temperature setpoint (operating range ~10–25 °C). |
| `LFC_RIO_I` | double, mA | RW 🔒 | RIO pump laser current (typical 145–155 mA). |
| `LFC_CLARITY_ONOFF` | enumerated | RW 🔒 | Clarity reference laser: reads 0 = off, 1 = on (the device's calibrating/locking/locked phases all read 1). Write 1/0 for on/off. |

## Shutters, routing, and attenuation

| Keyword | Type | Access | Description |
|---|---|---|---|
| `LFC_YJ_SHUTTER` | boolean | RW | YJ-band output shutter: 1 = pass, 0 = shut. |
| `LFC_HK_SHUTTER` | boolean | RW | HK-band output shutter: 1 = pass, 0 = shut. |
| `LFC_YJ_SHUT` | enumerated | RW | Legacy stub (the old handler did nothing); retirement proposed. |
| `LFC_2BY2_SWITCH` | enumerated | RW | Routes the flattened comb to the spectrograph: 1 = YJ, 2 = HK. |
| `LFC_VOA1550_ATTEN` | double, dB | RW | 1550 nm VOA (pump path) attenuation, 0–60 dB. |
| `LFC_VOA1310_ATTEN` | double, dB | RW | 1310 nm VOA (YJ comb path) attenuation. |
| `LFC_VOA2000_ATTEN` | double, dB | RW | 2000 nm VOA (HK comb path) attenuation. |

The old manual also lists convenience aliases (`LFC_YJ_ONOFF`,
`LFC_HK_ONOFF`, `LFC_PMP_ATT`, `LFC_YJ_ATT`, `LFC_HK_ATT`,
`LFC_YJ_HK`, `LFC_GET_TEMP`) — these were internal duplicates in the
old dispatcher, never part of the XML keyword list, and are not carried
into the rewrite.

## WaveShaper

| Keyword | Type | Access | Description |
|---|---|---|---|
| `LFC_WSP_PHASE` | double, ps/nm | RW 🔒 | Second-order dispersion (GDD, d₂) programmed onto WaveShaper 1. Applied together with `LFC_WSP_TOD` as one phase profile centered at the commissioned 1559.8 nm; reads report the value currently applied. Commissioned value: 2.14 ps/nm (d₃ = 0). |
| `LFC_WSP_TOD` | double, ps/nm² | RW 🔒 | Third-order dispersion (TOD, d₃) companion to `LFC_WSP_PHASE`. **New in the rewrite** (see `ktl/keyword-changes.md`). |
| `LFC_WSP_ATTEN` | double | RW 🔒 | Flat attenuation level (dB) applied across the WaveShaper profile. |

## TECs and temperatures

| Keyword | Type | Access | Description |
|---|---|---|---|
| `LFC_PPLN_T` | double, °C | RW 🔒 | PPLN doubler TEC setpoint (typical operating point ≈ 37 °C). Setpoint changes are ramped in 0.5 °C steps. |
| `LFC_WGD_T` | double, °C | RW 🔒 | Octave-waveguide TEC setpoint (typical 22.5–23.5 °C). Ramped. |
| `LFC_T_RACK_TOP` / `_MID` / `_BOT` | double, °C | RO | Rack air temperatures (waveshaper shelf / side baffle / power-supply shelf). |
| `LFC_T_GLY_RACK_IN` / `_OUT` | double, °C | RO | Rack glycol inlet / outlet. |
| `LFC_T_EOCB_IN` / `_OUT` | double, °C | RO | EO comb board glycol inlet / outlet. |
| `LFC_TEMP_TEST1` | double array | RO | All 8 thermocouples on DAQ board 0 (rack). |
| `LFC_TEMP_TEST2` | double array | RO | All 8 thermocouples on DAQ board 1 (optical table). |

## Transport and test keywords

| Keyword | Type | Access | Description |
|---|---|---|---|
| `ICESTA` | enumerated | RW | Dispatcher ↔ laptop-server connection state: 1 = connected, 2 = disconnected, 3 = (re)connect request. Name kept from the ICE era. |
| `ICESTA2` | boolean | RO | Secondary connection flag; retirement proposed. |
| `ICECLK` | string | RW | Server heartbeat (epoch seconds), poked ~1 Hz. |
| `ICECLK_ONOFF` | boolean | RW | Enable/disable the heartbeat monitor. |
| `ICETEST`, `TEST*` | various | RW | Dispatcher integration test keywords (soft values, no hardware). Retirement proposed. |
| `SHOW_ALL_VAL` | boolean | RW | Write 1: dump the full keyword snapshot to the server log. Reads False. |
