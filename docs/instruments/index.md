# Instrument Reference

```{toctree}
:maxdepth: 1

operations
```

The {doc}`operations` page collects the per-instrument operating
knowledge (setpoints, warm-up behavior, quirks, safety rules). The
full hardware inventory is in {doc}`../hardware/components`, and the
Python driver APIs are in the {doc}`../api/index`.

Tier-1 instruments (comb on/standby/off path and safety):

| Instrument | Driver | Role |
|---|---|---|
| Amonics EDFAs (13/23/27 dBm) | `amonics_edfa` | pump/pre-amplification |
| Pritel amplifier | `pritel_amp` | high-power amplification |
| ORION/RIO laser | `orion_laser` | seed laser |
| GW Instek supplies | `instek_psu` | RF oscillator + RF amplifier power |
| SRS FS725 | `rb_clock` | Rb frequency reference |
| Arduino relay | `arduino_relay` | Pritel interlock / latching relay |
| SRS SIM900 | `srs_sim900` | PID modules (IM bias lock) + voltage source |
| Finisar WaveShapers (×2) | `waveshaper` | spectral filtering / flattening |
| TC-720 | `tec_tc720` | TEC temperature control |
| USB-2408 | `usb2408` | thermocouple telemetry |
