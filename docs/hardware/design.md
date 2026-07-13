# Instrument design

How the astrocomb works, stage by stage. Adapted from the "Detailed
Instrument Design" chapter of the original manual.

```{figure} images/device_all.png
:width: 95%

Block diagram of the full system.
```

```{figure} images/device_all_connect.png
:width: 95%

System block diagram with interconnections.
```

## Comb generator

### Minicomb generation

The EO astrocomb uses three cascaded low-V<sub>π</sub> phase modulators
and an intensity modulator, driven by a low-phase-noise 16 GHz
oscillator locked to a GPS-referenced rubidium clock, to modulate an
amplified, frequency-stabilized 1560 nm pump laser. RF phase shifters
between the modulators optimize the minicomb — broad and flat,
approximately 11 nm wide. A variable RF attenuator tunes the RF power
driving the intensity modulator.

To overdrive the modulators, the oscillator signal is boosted in a
high-power (41 dBm) RF amplifier and split three ways, one channel per
phase modulator. Rejected RF power from one phase modulator drives the
intensity modulator and feeds the frequency counter that monitors the
repetition rate; residual power from the other two is dissipated in
50 Ω terminations.

A small tap of the minicomb goes to a photodetector that generates the
control signal for the minicomb **amplitude stabilization loop**: a
90:10 optical tap sends 10% of the light to a photodetector, and an SRS
SIM960 servo feeds back a bias voltage (−10 to +10 V) to the intensity
modulator to hold the minicomb amplitude constant.

The main minicomb signal is amplified in an EDFA, sent through the
dispersion-compensation WaveShaper, amplified again, then split in a
polarizing beam splitter. The fast-axis light is reserved for the
(currently unused) filter-cavity loop; the slow-axis light continues to
the high-power Pritel EDFA, where it is amplified to roughly 4 W.

```{figure} images/eocomb_setup.png
:width: 85%

Minicomb (EO comb) setup.
```

```{figure} images/eocomb_signalchain.png
:width: 65%

Minicomb signal chain.
```

```{figure} images/eocomb.png
:width: 85%

Minicomb spectrum (~11 nm wide when optimized).
```

### Supercontinuum generation

The pulse is temporally compressed in normal-dispersion
(−2.3 ps/nm/km) highly nonlinear fiber (HNLF) spliced to PM-1550 fiber
— the pulse-compression stage, housed in a small aluminum chassis on
the breadboard. The compressed pulse enters a temperature-controlled
tantala (Ta₂O₅) waveguide, where nonlinear processes broaden the
minicomb spectrum to well over an octave.

Spectra from the summit test of 2025-05-17:

```{figure} images/2mhnlf_full_comb.png
:width: 85%

Spectrum directly from the waveguide.
```

```{figure} images/final2m_hnlf_full_comb.png
:width: 85%

Spectrum before the Menlo flattener.
```

```{figure} images/menlo_2500nm.png
:width: 85%

Full spectrum displayed on the Menlo flattener.
```

## Frequency stabilization

The comb has two sources of frequency instability to control:

- The **repetition rate** (line spacing) is set by a 16 GHz phase-locked
  dielectric resonant oscillator (PLL-DRO), stabilized at 10 MHz by a
  GPS-disciplined rubidium clock (SRS FS725).
- The **pump laser** may be stabilized to one of three references,
  described below.

### Clarity laser

The current pump reference is the Clarity laser (Wavelength
References, HCN-stabilized at 1559 nm), with stability corresponding to
~30 cm/s radial-velocity precision at ~100 s.

```{figure} images/clarity.jpg
:width: 60%

The Clarity laser.
```

### Rubidium-locked RIO laser

The pump frequency can instead be locked to a rubidium transition at
780 nm: a portion of the amplified 1560 nm pump is frequency-doubled in
a PM fiber-coupled PPLN (Covesion), and the 780 nm light drives a
Vescent D2-210 Rb spectroscopy module whose RF output peaks when the
doubled frequency matches the absorption line. A PDH control loop
(4 MHz phase-modulation dither, mixed down and fed to a SIM960)
adjusts the pump laser to hold the frequency.

The technique works well when the system has been running continuously,
but not from a cold start: the RIO Orion pump (1 kHz linewidth, center
1560.492 ± 0.015 nm) drifts significantly when unlocked. Two tuning
handles exist — laser current ("coarse", full ~30 pm range, risk of
mode hops if swept fast) and a voltage bias on the modulation port
("fine", for narrow sweeps). The Covesion doubler temperature must
track frequency changes to keep transmission maximized.

```{figure} images/rb_configureation.png
:width: 85%

The Rb locking assembly.
```

The automatic lock algorithm compares the present sweep against stored
template traces by convolution, iteratively narrowing the sweep range
until a single monotonic slope of the fine absorption line remains,
then locks to its center. The full procedure — manual and automatic —
is in {doc}`../user_guide/locking`.

### Fiber-comb referenced pump

Long-term stability measurements can be made by heterodyning a portion
of the comb output against the red-arm output of the Keck Planet
Finder (KPF) astrocomb (Menlo Systems); the ~1100–1400 nm overlap
region is optimal.

## Filter cavity (deprecated)

The comb was designed to suppress RF phase noise by passing the
amplified minicomb through a 16 GHz free-spectral-range Fabry–Perot
cavity locked to the pump laser (using the fast-axis PBS output).
Commissioning showed the stage to be unnecessary — comb lines were
unresolved (sub-pixel) across all observable NIRSPEC orders — so it is
bypassed, though the components remain on the breadboard.

Points to know if it is ever reinstated:

- Line filtering *is* required for self-referencing an EO comb (f–2f);
  this instrument is line-referenced instead.
- Minimizing cavity insertion loss requires the RF oscillator to be
  offset from 16 GHz by ≈1.2 MHz when the pump is Rb-locked. The Nexyn
  oscillators would have to go back to the vendor for retuning, and the
  offset should be re-verified beforehand with a variable RF source.
- The cavity's piezo controller and TEC supply are currently
  disconnected from power.

## Thermal control

A glycol-cooled heat exchanger at the bottom of the rack expels cool
air toward the bench-facing side and draws hot air in from the outward
side. A chimney with an inlet at the top of the rack pulls warm air
down via a mid-duct fan into the heat-exchanger intake; two fans midway
up the bench-facing side push warm air the same way. All rack shelving
is perforated.

The breadboards are glycol-cooled from the same facility source (the
chiller is in an adjacent basement room). Sixteen thermocouples across
the board and rack feed the two USB-2408 DAQ modules — the channel
maps are in the {doc}`../instruments/index` section and in
`keckogeco`'s config.

## Spectral flattener

The flattener (Menlo Systems) equalizes comb-line intensity across the
bandpass. Light enters through a fiber collimator, disperses off a
reflective grating onto a liquid-crystal-on-silicon (LCoS) spatial
light modulator, one pixel per comb line. Each pixel rotates the
polarization of its line in proportion to applied voltage; the return
pass through a polarizer converts that to per-line attenuation. The
voltage "mask" is generated by feedback from the internal Arcoptix
spectrometer. The flattened spectrum passes through a user-selectable
ND filter slider and a shutter into the output fiber toward the dome.

```{figure} images/thumbnail_image3.jpg
:width: 75%

The Menlo flattener unit.
```

```{figure} images/thumbnail_image2.jpg
:width: 75%

Inside the flattener: grating, SLM, and filter slider.
```

Operation of the flattener software is covered in
{doc}`../user_guide/menlo_flattener`.

## Fiber feed

All comb fiber connections use polarization-maintaining Panda 1550
fiber, nearly all with narrow-key PM FC/APC connectors. The
rack-interface fiber map is in {doc}`components`. The facility feed to
the dome is presently SM28 fiber, with ZBLAN planned.

## Electrical

Two 24-outlet Eaton EMA1140-10 managed PDUs (strips A and B, 16 A
breakers) power the rack; they report per-outlet power draw over the
rack network. The full outlet assignment table is in
{doc}`components`. The UPS units under the interface panel carry the
system through short facility power interruptions.
