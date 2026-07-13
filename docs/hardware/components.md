# Component inventory

Every numbered component in the annotated assembly photo, with model,
control interface, location, power source, and role. ePDU ports refer
to the two Eaton power strips ("A" left, "B" right, outlets 1–24
top-to-bottom); see the [power distribution table](#power-distribution)
below.

```{figure} images/photo_indicate.jpg
:width: 100%

The astrocomb hardware with numbered components.
```

## Items 1–10: front-end RF and minicomb

| # | Component | Model | Control | Location | Power | Role |
|---|---|---|---|---|---|---|
| 1 | Signal generators | Keysight 33500 series (×2): 33512B s/n MY62003852 + s/n MY59003824 | USB/LAN (`keysight_fg33500`) | Rack | A3, A7 | Ramps and modulation tones for the Rb lock and test signals. |
| 2 | WaveShaper | II-VI/Finisar WS-01000A-C-R-1-AA-01 | USB, vendor API (`waveshaper`) | Rack | A5 | Programmable phase/amplitude shaping of the EO minicomb (pulse compression, spectral tailoring). |
| 3 | Small component chassis | Custom | Arduino + dedicated drivers | Rack | Local LV rails | Safety interlock chain for the Pritel EDFA; houses VOAs and shutters. See below. |
| 4 | High-power EDFA | Pritel LNHP-PMFA-37-IO-NMA | Serial (`pritel_amp`) | Rack | A2 (2 A) | Main high-power EDFA feeding supercontinuum generation. Spare stored at Caltech (Watson lab). |
| 5 | 23 dB EDFA | Amonics AEDFA-PM-23-R-FA | Serial (`amonics_edfa`) | Rack | B3 (~1.5 A) | Amplifies the EO minicomb after modulation. |
| 6 | 27 dB EDFA | Amonics AEDFA-PM-27-R-FA | Serial (`amonics_edfa`) | Rack | A4 (~2.5 A) | Pump amplifier for referencing + EO comb generation. |
| 7 | 13 dB EDFA (spare) | Amonics AEDFA-PM-13-R-FA | Serial (`amonics_edfa`) | Rack | B6 | Spare low-gain EDFA for future upgrades. |
| 8 | Servo mainframe | SRS SIM900 + SIM960 PIDs + SIM928 | GPIB (`srs_sim900`) | Rack | A6 (≤70 W) | PID control for the IM amplitude lock and Rb lock loops. |
| 9 | Frequency counter | Pendulum CNT-90XL | GPIB (`pendulum_cnt90`) | Rack | B4 | Counts the 16 GHz signal → comb repetition rate. Owned by JPL. |
| 10 | LFC laptop | Windows laptop | Ethernet | Rack | B17 | Runs the `keckogeco` server and GUIs. |

## Items 11–27: optical diagnostics and rack infrastructure

| # | Component | Model | Control | Location | Power | Role |
|---|---|---|---|---|---|---|
| 11 | Optical spectrum analyzer | Agilent/HP 86142B | GPIB (`agilent_86142b`) | Rack | A13 | Monitors minicomb and broadened comb spectra. |
| 12 | Filter cavity piezo controller | TBD | Local only | Rack | Local supply | For the (currently unused) filter cavity; disconnected. |
| 13 | Rb spectroscopy supply | Vescent D2-005 | Local panel | Rack | A18 | DC supply for the D2-210 Rb module. |
| 14 | DC supply 1 | GW Instek GPD-4303S | Serial (`instek_psu`) | Rack | A19 | RF oscillator power (ch 2) and IM RF-attenuator bias (ch 3). |
| 15 | Chimney fan | — | None | Rack | Facility panel | Rack airflow. |
| 16 | Ethernet router | TP-Link | Web UI | Rack | B8 | Internal routing: laptops + ethernet instruments. |
| 17 | Rubidium clock | SRS FS725 | Serial (`rb_clock`) | Rack | B5 (50 W) | 10 MHz GPS-disciplined reference for the RF chain and counter. |
| 18 | USB hub A | 15-port powered | — | Rack | A15 | USB aggregation (see hub table below). |
| 19 | USB hub B | 15-port powered | — | Rack | B23 | Additional USB capacity. |
| 20 | Ethernet switch | Unmanaged | — | Rack | B21 | Local switching. |
| 21 | DAQ 2 | MCC USB-2408 | USB (`usb2408`, board 0) | Rack | USB | Rack thermocouples. |
| 22 | Filter cavity TEC | Wavelength Electronics LFI-3751 | Local/analog | Rack | A10 | Filter-cavity temperature (cavity unused; disconnected). |
| 23 | FPGAs | Red Pitaya STEMlab 125-14 (×3) | Ethernet SCPI | Rack | A24, B24, B20 | Historically 10 MHz/lock signal generation; being phased out for the Keysight FGs. |
| 24 | DC supply 2 | GW Instek GPP-1326 | Serial (`instek_psu`) | Rack | A20 | 30 V / high-current supply for the RF amplifier. One unit was replaced by an Acopian after a failure. |
| 25 | Heat exchanger | Liquid-cooled | None | Rack cooling panel | A22 | Removes heat from the RF amp and EDFA plate. |
| 26–27 | Web PDUs | Eaton EMA114-10 (×2) | Ethernet web UI (`eaton_pdu`) | Interface panel | Feed-through | Remotely switchable AC outlets for everything in the rack. |

## Items 28–40: RF chain and EO modulators

| # | Component | Model | Control | Location | Power | Role |
|---|---|---|---|---|---|---|
| 28 | RF oscillator | Nexyn 16 GHz PLL-DRO | Via its DC supply | Bench | DC supply 1 ch 2 | Primary 16 GHz drive tone. |
| 29 | RF amplifier | CTT/Kratos GaN power amp | Via its DC supply | Bench, cooled panel | DC supply 2 (30 V, ~4–5 A) | Amplifies the 16 GHz drive (41 dBm) for the modulators. |
| 30 | RF amplifier (spare) | CTT/Kratos GaN | — | Bench / A21 | — | Replacement for item 29. |
| 31 | RF splitter | Broadband splitter | Passive | Bench | — | Distributes RF to the three phase modulators. |
| 32–34 | RF phase shifters | Adjustable, 16 GHz | Manual knobs | Bench | Passive | Optimize RF phase alignment across modulators. |
| 35–37 | EO phase modulators | EOSpace PM-5SES-20-PFA-PFA-UV(-SOP1W)-UL | RF-driven | Bench | — | Generate the phase-modulated sidebands of the minicomb; one carries a small programmable RF attenuator. |
| 38 | Intensity modulator | EOSpace AX-0MSS-20-PFA-PFA-LV-UL (20 GHz LiNbO₃) | RF drive + DC bias | Bench | — | Pulse carving and the IM-bias lock actuator. |
| 39–40 | RF/EO misc | Cabling, terminations | Passive | Bench | — | Complete the modulation chain. |

## Items 41–56: pump laser, nonlinear stages, sensing

| # | Component | Model | Control | Location | Power | Role |
|---|---|---|---|---|---|---|
| 41 | Pump laser | Rio/Luna ORION RIO0175-5-01-4-AW2 | Serial (`orion_laser`) | Bench enclosure | A9 (5 V, 16 W) | Main 1560 nm pump, normally locked to the Rb reference. |
| 42 | Filter cavity TEC (spare) | SLS TEC controller | Local | Bench (B3) | Local | Unused. |
| 43 | Circulator | Fiber circulator | Passive | Bench | — | Routing through filter cavity / Rb module. |
| 44 | AM photodetectors | ×3 (two on board, one in chassis) | Analog out | Bench + chassis | B18, B19, A17 | Amplitude monitors for the IM lock and diagnostics. |
| 46 | Pump frequency doubler | Covesion WGHP-S-1560-40 SHG waveguide | Thermal (TEC) | Bench | — | Doubles 1560 → 780 nm for Rb spectroscopy. |
| 47 | Rb spectroscopy module | Vescent D2-210 | Local + analog | Bench | Via D2-005 | Absorption/PDH signals for the Rb lock. |
| 48 | Rb lock phase modulator | EOSpace PM | RF from FG | Bench | — | PDH dither on the Rb line. |
| 49–50 | Pulse compression stages | Home-built OFS HNLF chassis (+ spare) | Passive | Bench | — | Nonlinear compression before the waveguide. |
| 51–53 | SCG waveguides | Ta₂O₅ supercontinuum waveguides (1 active, 2 spare) | Passive | Bench | — | Broadband spectrum via supercontinuum generation. |
| 54 | WDM | 1550/2000 nm pump filter | Passive | Bench | — | Separates pump and long-wavelength comb light. |
| 55 | DAQ 1 | MCC USB-2408 | USB (`usb2408`, board 1) | Bench | USB | Optical-table thermocouples. |
| 56 | TEC controllers | TE Tech TC-720 OEM (×2) | Serial (`tec_tc720`) | Bench | A16 (12 V) | PPLN doubler and SCG waveguide temperatures. |

## Items 57–70: auxiliary components

| # | Component | Model | Control | Location | Power | Role |
|---|---|---|---|---|---|---|
| 57 | USB hub 2 | 7-port powered | — | Bench | Local adapter | Breadboard USB aggregation. |
| 58 | SLM | Santec (inside Menlo flattener) | Flattener laptop | Flattener | B16 | Per-line amplitude shaping in the flattener. |
| 59 | Spectrometer | Arcoptix FTNIR-L1-025-2TE | USB | Flattener | B13 | Monitors the flattener output spectrum. |
| 60 | Shutter | Thorlabs | Via controller (64) | Bench | — | Beam block for flattener/diagnostic paths. |
| 61 | Filter slider | Thorlabs motorized | Controller interface | Bench | B22 | Inserts ND filters in the flattener path (the 0–60 dB output attenuation steps). |
| 62 | Oscilloscope | HP scope | GPIB/USB possible | Bench | A12 | Rb spectroscopy / PDH signals. **Occasionally freezes — power-cycle via ePDU**, not just the front panel. |
| 63 | Line-referenced pump | Clarity 1560-HP NLL (Wavelength References) | Serial (`clarity`) | Bench | A23 | Alternative pump reference (~30 cm/s stability class); used when not running the RIO+Rb lock. |
| 64 | Shutter controller | Thorlabs | Digital interface | Bench shelf | A11 | Drives item 60. |
| 65 | Flattener laptop | Dedicated laptop | Ethernet (remote desktop) | Near flattener | B9 | Runs Menlo software, SLM, spectrometer. |
| 66 | USB hub 1 | 4-port | — | Interface panel | Local adapter | Flattener-subsystem USB. |
| 67 | Webcam | USB webcam | Host PC | Above table | USB | Visual monitoring of the optics. |
| 68 | Autocorrelator | Femtochrome FR-103XL | Local | As needed | A14 | Pulse-duration measurements (on loan, JPL/Caltech). |
| 69 | Rack muffin fans | Side-wall pair | ePDU on/off | Rack side | B12 | Airflow / thermal stability. |
| 70 | Desktop monitor | External monitor | — | Desk | B14 | For the laptops' GUIs. |

**Candidate future hardware:** SRS SIM983 ±10 V scaling amplifier for
the SIM900 rack (not installed).

## Small component chassis

Contents of item 3 and closely associated rack hardware:

| Component | Model / notes | Power | Function |
|---|---|---|---|
| Interlock controller | Arduino-based | B1 | Monitors Pritel input power; trips the interlock when out of range. |
| Interlock latching circuit | Custom | A13 | Once tripped, keeps the Pritel disabled until explicitly reset (`LFC_PTAMP_LATCH`). |
| Interlock cables | Dedicated set (3 spares in the "Spares Box") | — | Carry the interlock signals to the Pritel interlock ports. |
| VOA-1550 / VOA-1310 / VOA-2000 | OZ Optics VOAs (pump / YJ / HK paths) | B7 (5 V) | Programmable attenuation. **The chassis is currently bypassed in the live optical loop**, so these do not affect the beam. |
| 2×2 optical switch | Agiltron FFSW-222C00323 MEMS latching | Via driver | Routes YJ or HK comb to the spectrograph (`LFC_2BY2_SWITCH`). |
| 2×2 switch driver | Agiltron SWDR-111111121 (USB/TTL/push-button) | Rack LV | Drive and control for the switch. |
| YJ shutter | Agiltron fiber shutter | B11 | YJ output shutter (`LFC_YJ_SHUTTER`). |
| RF mixer / RF filter | Passive | — | RF conditioning in the chain. |

## USB hub assignments

| Hub A port | Device | Hub B port | Device |
|---|---|---|---|
| 1 | GPIB adapter (SIM900) | 3 | Filter cavity TEC |
| 2 | RF amp 1 supply | 6 | DAQ USB-2408 #1 |
| 3 | RF osc supply | 15 | RJ45–USB adapter |
| 4 | RF amp 2 supply | others | Spare / unused |
| 5 | Keysight function generator | | |
| 6 | Amonics 27 dBm | | |
| 7 | Amonics 13 dBm | | |
| 8 | Pritel | | |
| 9 | WaveShaper | | |
| 10 | Rb clock | | |
| 11 | Amonics 23 dBm | | |
| 12 | Eaton PDU (left) | | |
| 13 | Latching circuit | | |
| 14 | TP-Link router | | |
| 15 | Spare | | |

```{note}
Physical hub ports are informational only — `keckogeco` never relies on
them. Discovery anchors each instrument to its USB adapter serial
number, so devices keep working when re-plugged into different ports.
```

(power-distribution)=
## Power distribution (ePDU outlets)

Two Eaton EMA1140-10 managed PDUs (16 A breaker each) on the rack,
strips "A" (left) and "B" (right), outlets numbered top to bottom.
They report per-outlet power draw and are on the rack ethernet switch.

| Port | Strip A | Strip B |
|---|---|---|
| 1 | Photodetector supply (Pritel interlock) | Relay circuit supply |
| 2 | Pritel EDFA | RF amplifier supply (chassis) |
| 3 | Keysight function generator | Amonics 23 dBm EDFA |
| 4 | Amonics 27 dBm EDFA | Pendulum frequency counter |
| 5 | Finisar WaveShaper | SRS rubidium clock |
| 6 | SRS mainframe | Amonics 13 dBm EDFA |
| 7 | Keysight function generator #2 (ePDU label "new Keysight function generator") | VOAs (small chassis, ×3) |
| 8 | Spare | Internet router |
| 9 | RIO laser supply | Menlo flattener laptop |
| 10 | FC temperature controller | Spare |
| 11 | Thorlabs shutter controller | YJ fiber shutter controller |
| 12 | HP oscilloscope (bench) | Rack muffin fans |
| 13 | Agilent OSA | Arcoptix spectrometer (flattener) |
| 14 | Autocorrelator (temporary) | LFC computer monitor |
| 15 | USB hub A | Bench OSA (temporary) |
| 16 | Waveguide TEC supply | Santec SLM (flattener) |
| 17 | Photodetector #3 supply | LFC laptop charger |
| 18 | Vescent D2-005 supply | Photodetector #2 supply |
| 19 | GW Instek supply (RF oscillator) | Photodetector #1 supply |
| 20 | GW Instek supply (RF amplifier) | Red Pitaya 072A9 |
| 21 | Spare RF supply | Ethernet switch |
| 22 | Heat exchanger | Filter slider (flattener) |
| 23 | Clarity | USB hub B |
| 24 | Red Pitaya 072EC | Red Pitaya 0602C |

Two rack components are deliberately disconnected from power: the
filter-cavity piezo controller and the filter-cavity TEC supply.

## Rack interface panel connections

Optical fiber (FC/APC, PM Panda 1550 with narrow-key connectors):

| Row | Port 1 | Port 2 | Port 3 | Port 4 |
|---|---|---|---|---|
| A | Broad comb output to chassis/VOA | Labeled "comb output to VOA" (actually to benchtop OSA) | — | — |
| B | — | — | — | 23 dBm EDFA → FC |
| C | Minicomb in → OSA | FC in → OSA monitor | Doubler in → OSA monitor | FC → Pritel relay |
| D | Pritel → HNLF compression stage | Minicomb → WaveShaper | 27 dBm EDFA → phase modulators | RIO → 27 dBm EDFA |

Coax:

| Row | Port 1 | Port 2 | Port 3 | Port 4 |
|---|---|---|---|---|
| A | FC PD → mixer | FG → FC phase modulator | IM DC bias → SRS PID | Minicomb photodiode → SRS PID |
| B | FG → Rb phase modulator | SRS → RIO laser feedback | Spare | FC piezo controller → FC |
| C | Rb cell → PDH | Temporary Rb PDH | Temporary Rb PDH | 10 MHz FS725 → 16 GHz RF oscillator |
| D | Oscilloscope ch 4 | Oscilloscope ch 3 | DC → IM RF attenuator | — |

SMA: port 1 = frequency counter input; ports 2–4 spare.
