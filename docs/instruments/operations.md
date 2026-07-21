# Operational notes per instrument

The accumulated operating knowledge for each instrument — warm-up
behavior, commissioned setpoints, quirks, and safety rules — adapted
from the original manual's "from the rack to the comb" chapter and
updated for `keckogeco`. Read this before operating anything by hand.

Code examples use the `keckogeco` driver APIs (via
`python -m keckogeco.check --device <key>` sessions or the REST API);
routine operation should go through the keywords and transition
actions instead.

## Clarity reference laser

- Contains its own internal reference, nominally stable at the 10⁻⁹
  level — sufficient as a stand-alone reference for most purposes.
- Needs a warm-up after power-on; the front panel walks through
  power-on → locking → **locked**. Only use it as a reference once
  locked. (`LFC_CLARITY_ONOFF` reads 1 in any of the three active
  phases.)
- The front-panel laser button is password-protected; the code is kept
  in the private operations notes. Don't change it without
  coordination.
- The lock-point selection among parts of the error signal is not
  critical for this application.
- **Before cutting rack power, always turn off the Clarity laser
  activation on the front panel** — never pull power with the diode
  driven.

## RIO seed laser

- Emits as soon as it is plugged in and enabled; allow a few minutes of
  warm-up.
- Free-running stability is insufficient for astrocomb use: in regular
  operation it is locked to the Rb reference
  ({doc}`../user_guide/locking`). When locked, stability is comparable
  to or better than the Clarity.
- Software-only control. Two handles: **diode current** (fast,
  small-range frequency tuning; `LFC_RIO_I`, typical 145–155 mA) and
  **TEC temperature** (slow, large-range; `LFC_RIO_T`). Allow
  re-stabilization time after either changes.
- Occasional **mode hops** are a property of the device, not a software
  fault.

In practice: Clarity for quick standalone work; RIO + Rb lock when a
tighter, Rb-referenced frequency is required.

## RF oscillator and its supply (Instek GPD-4303S)

Only channels 2 and 3 of the four-channel supply are used:

- **Channel 2 powers the 16 GHz oscillator**: 15 V, drawing ~0.6 A
  (≈0.41 A steady-state after warm-up); configured limits 15 V / 3 A.
- **Channel 3 is the RF attenuation control voltage** for the intensity
  modulator (`LFC_IM_RF_ATT`), normally around 0.80–0.85 V with a 1 A
  limit. The attenuator is an RF-Lambda RFVAT0218R30: 0–10 V control
  (matches the keyword range), 15 V absolute maximum — the GPD can
  source 30 V, so never raise the keyword ceiling past 10 V.
- Channels 1 and 4 are unused.
- **Before turning off mains power, disable the DC outputs first**,
  then the front-panel power.

```python
rf_osc.set_voltage_V(15, channel=2)
rf_osc.set_current_A(3, channel=2)
rf_osc.set_voltage_V(0.8, channel=3)
rf_osc.set_output(True, channel=2)
```

## RF amplifier and its supply (Instek GPP-1326)

- Two GaN amplifier modules sit on the cooled panel (one spare). If a
  module is ever replaced, re-apply a thin uniform layer of thermal
  compound before tightening the screws.
- Normal supply configuration: **30 V / 4.2 A** (~120 W in a healthy
  state). If voltage or current deviates significantly, switch the
  supply off immediately — a previous power outage destroyed an
  amplifier that was left at high current.
- Startup order when bringing the comb up by hand: **RF amplifier
  supply first, then the RF oscillator supply.** Bring the amplifier
  supply directly to 30 V — do not slowly ramp the voltage through a
  large range while RF drive is present. (The `keckogeco` transition
  sequences follow this order.)

## Amonics 27 dBm EDFA

- Default mode: **APC at 450 mW** (`LFC_EDFA27_P_DEFAULT`).
- Amplifies the seed before the EO modulation chain — excessive power
  here can damage downstream components.

```python
edfa27.set_mode("APC")
edfa27.set_setpoint(450)
edfa27.set_channel(True)
edfa27.activate()
```

## Amonics 23 dBm EDFA

- Amplifies the EO comb before the Pritel stage. Commissioned setting:
  ACC 80 mA — use just enough to reach the desired bandwidth after the
  nonlinear stages, and watch temperatures and interlocks when raising
  it.
- **Currently out of service**: the transition sequences and default
  keywords park it at ACC 0 mA (activated but dark).

## Phase modulators, intensity modulator, phase shifter

- The three PMs and the IM run at fixed RF drive and bias points; no
  routine adjustment.
- The passive phase shifter sets the EO comb bandwidth: typically only
  one knob combination maximizes the width. If the comb bandwidth
  shrinks noticeably, small phase-shifter adjustments may recover it;
  otherwise leave it alone.

## Optical spectrum analyzer (Agilent 86142B)

- Narrow span around the carrier (e.g. 1553–1567 nm) to judge EO comb
  flatness and sideband structure; wide span with more input
  attenuation for the broadened comb.

```python
osa.wl_start_nm = 1553
osa.wl_stop_nm = 1567
wavelength, power = osa.get_spectrum("A")
```

The GUI's spectra panel and `/api/v1/arrays/osa_spectrum` expose the
same trace.

## WaveShaper

- Programs the spectral phase that compresses the EO comb toward a
  Fourier-limited pulse (`LFC_WSP_PHASE` = d₂ in ps/nm).
- Rough starting points: **d₂ ≈ +2** when the IM bias is locked
  slightly below center of the negative RF slope; **d₂ ≈ −3** near the
  positive slope. The true optimum depends on IM bias, power, and
  temperature — always verify against the Menlo spectrum.
- Third-order dispersion is kept at **d₃ = 0**; nonzero values
  empirically worsen the pulse.
- The bandpass setting rarely changes.
- The device speaks its own DLL API (not VISA) and tolerates only one
  connection at a time. The `keckogeco` server owns that connection —
  don't run vendor tools or notebook sessions against the WaveShaper
  while the server is up.

## Arduino interlock and YJ shutter

- Two jobs: enforce the Pritel input-power interlock, and drive the YJ
  shutter (`LFC_YJ_SHUTTER`).
- On disconnect or system close, the interlock drops to its safe *off*
  state and blocks the amplifier. Only with adequate Pritel input power
  and an explicit latch reset (`LFC_PTAMP_LATCH = 1`) does it move to
  standby and allow Pritel activation.
- The hardware latch survives software restarts — restarting Python (or
  the server) does **not** reset it.

## Pritel main amplifier

The final high-power stage before the nonlinear fiber and the tantala
waveguide — the most safety-critical device in the system.

- Typical operating points: preamp **600 mA**; power amp **3.8–4.2 A**.
- The hardware limit is 5 A, but operating near it is **very risky**
  for the optics and fiber. Do not exceed 4.2 A without a clear,
  understood reason and supervision.
- The `keckogeco` driver ramps every current change in steps; the
  transition sequences reset the interlock latch, set the preamp,
  enable the pump, then ramp the power amp.

## 2×2 optical switch

- Position 1 = YJ path, position 2 = HK path (`LFC_2BY2_SWITCH`).
- Always confirm the switch position before opening the corresponding
  shutter and adjusting instrument exposure.

## HK shutter

- 1 = open (HK comb passes), 0 = closed (`LFC_HK_SHUTTER`). A simple
  on/off device on its own serial protocol.

## Oscilloscope

- Observes the Rb spectroscopy signals: channel 1 = transmission
  through the Rb cell, channel 2 = PDH error signal.
- **The scope occasionally freezes.** When it does, power-cycle it via
  its ePDU outlet (A12) — the front-panel button is not enough.

## Function generators (Keysight 33500B ×2)

- **First generator**: channel 1 provides the slow frequency ramp for
  the laser (the sweep used to find Rb lines); channel 2 unused.
- **Second generator**: channels 1 and 2 provide the PDH modulation and
  demodulation signals.

```python
fg1.set_function(1, "ramp")
fg1.set_frequency_Hz(1, 10)
fg1.set_amplitude_V(1, 8)
fg1.set_output(1, True)
```

## VOAs (1550 / 1310 / 2000 nm)

- Programmable attenuators for the pump, YJ, and HK paths, mounted in
  the small component chassis.
- **The chassis is currently bypassed in the live optical loop** — the
  VOA settings do not affect the beam. The chassis wiring is dense;
  don't open or modify it unless actively reworking that subsystem.
- After a power cycle a VOA reports `Atten:unknown` until its first
  move; `keckogeco` reads that as NaN until an attenuation is set.

## TEC controllers (TC-720 ×2)

- **PPLN TEC** sets the phase-matching window of the 1560 nm SHG and
  aligns the doubled light to the Rb D2 transition (`LFC_PPLN_T`;
  operating points have ranged ~37–42 °C depending on calibration).
  Once aligned, rarely changed.
- **Waveguide TEC** typically sits near 23–25 °C (`LFC_WGD_T`). The
  water-cooled breadboard is colder than room temperature, so the TEC
  initially *heats*; once the comb is on and absorption rises it
  crosses over to *cooling* — seeing that transition is a good check
  that regulation works.
- Keyword writes ramp the setpoint in 0.5 °C steps automatically (the
  old manual's hand-rolled ramp loop is built in).

## Thermocouples and DAQ boards (USB-2408 ×2)

| Channel | Serial 205F843 — electronics rack (`daq`) | Serial 205F82F — optical table (`daq_eocb`) |
|---|---|---|
| 0 | Rack side baffle (mid rack) | RF oscillator |
| 1 | WaveShaper (upper rack) | RF amplifier |
| 2 | Rb clock (mid rack) | Main phase modulators |
| 3 | Pritel (mid-upper rack) | Filter cavity |
| 4 | Rack glycol out | Board glycol out |
| 5 | Rack glycol in | Board glycol in |
| 6 | Power-supply shelf (bottom) | Compression stage |
| 7 | Unused | Rb cell (D2-210) |

`LFC_TEMP_TEST1` / `LFC_TEMP_TEST2` dump all eight channels of the rack
board / table board respectively; the named `LFC_T_*` keywords map to the
individual channels shown above. Boards are addressed by USB serial
number in the config and bound directly through `mcculw` — InstaCal
never needs to be run.
