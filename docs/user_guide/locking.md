# Locking procedures (expert)

The manual procedures behind the comb's two servo loops: the intensity
modulator (IM) bias lock and the rubidium lock of the RIO pump laser.
The IM lock is **deliberately manual** (design decision 2026-07-15,
replacing the old `LFC_IM_AUTO_LOCK` automation): the GUI's IM Bias
Lock tab runs the scan and *suggests* a setpoint, starting bias, and PI
gains, but the operator enters them in the servo panel and engages the
lock (`LFC_IM_LOCK_MODE`). The Rb lock is likewise **not** automated in
`keckogeco` — the manual procedure and the old system's auto-lock
algorithm are preserved here as reference.

## Flattening the EO comb and locking the IM bias

### 1. View the EO comb

Watch a narrow span around the carrier on the OSA (e.g.
1553–1567 nm) — via the GUI spectra panel or the driver directly.
Adjust input attenuation/resolution until the comb structure is clear.

### 2. Put the IM servo in manual mode

Set `LFC_IM_LOCK_MODE = 0` (SIM960 to manual output).

### 3. Scan the IM bias for a good slope

Sweep the IM bias over roughly −3 V to +3 V (`LFC_IM_BIAS`, or the
servo's manual output) and find a point on a reasonably linear slope of
the transfer function where the EO comb looks flattest. Record the
bias voltage.

The GUI's **IM Bias Lock** tab automates this step: it sweeps the bias
and plots the minicomb photodiode (the servo's measure input) against
it, restoring the pre-scan bias afterwards, and prints suggested lock
settings (the mid-fringe crossing nearest the panel's Bias start value,
the lockpoint there, and PI gains scaled from the measured slope). A
purple dot marks where the servo currently sits on the curve — live at
all times, including mid-scan — and a saved calibration scan can be
overlaid as a dashed reference (remembered across GUI restarts, like
the OSA reference spectrum). The scan runs on the server's action
executor, so Abort stops it and transitions can't run concurrently.

```{warning}
The transfer function's amplitude scales with the EDFA27 power, and at
the commissioned 450 mW the lock photodetector clips above ~5.5 V (see
the {doc}`design description <../hardware/design>`). Scan and choose
lock settings at the power you intend to operate at.
```

### 4. Adjust RF attenuation and bias together

Iterate between the IM bias and the RF attenuation voltage
(`LFC_IM_RF_ATT`, channel 3 of the RF oscillator supply, normally
0.80–0.85 V) while watching the OSA, until the minicomb around the
carrier is satisfactorily flat.

### 5. Engage the PID

At the chosen operating point, enter in the servo panel (or via the
API):

- the **lockpoint** — the photodiode voltage to hold (the scan
  suggestion is the mid-fringe value; adjustable even while locked);
- the **Bias start** — where the lock starts from;
- **proportional gain negative when locking on a falling slope,
  positive on a rising slope** (e.g. −2), integral gain ~0.1;
- then press **Lock**: the GUI writes the Bias start to `LFC_IM_BIAS`
  and engages `LFC_IM_LOCK_MODE = 1`, which copies that bias into the
  SIM960 output offset so the PID takes over bumplessly. While locked,
  the Bias out box is read-only and follows the PID's live output.

### 6. Sanity-check the lock

Watch the servo output and error input for ~20 s: the loop should
converge, not run away. If the output ramps toward a rail or the error
doesn't shrink, reduce the gains and retry. **Never leave an unstable
PID loop running while the comb is used for science.**

```{note}
The old system's `LFC_IM_AUTO_LOCK` automated this sequence (bias
sweep → slope-sign detection → mid-fringe setpoint → PID engage).
The rewrite retires it deliberately: the scan-and-suggest workflow
keeps the operator's judgment in the loop, and the keyword is unbound.
```

## Rb lock of the RIO laser

Stabilizes the RIO frequency to a feature of the Rb D2 line at 780 nm
via second-harmonic generation in the PPLN and a PDH scheme. See the
{doc}`design description <../hardware/design>` for the optical layout.

### Initial temperature conditions

The PPLN setpoint from previous calibration is typically ~42 °C but
drifts. Starting from a warm system near the last known working points
is fast; a fully cold start requires a slow two-dimensional search in
laser temperature and PPLN temperature.

### Finding the absorption features

1. Drive the laser with a low-frequency ramp from function generator 1
   (channel 1) and watch the oscilloscope: channel 1 shows the Rb cell
   transmission (broad Lorentzian envelope with fine substructure),
   channel 2 the PDH signal.
2. Turn on the modulation/demodulation paths on function generator 2,
   then adjust modulation amplitude and phase until the PDH signal has
   the expected dispersive shape around the target sub-feature.
3. Gradually reduce the sweep amplitude so the scan covers only the
   region around the target fine structure — ideally running almost
   entirely on the chosen slope.

Remember the doubler temperature must track any significant laser
frequency change to keep SHG transmission up, which slows wide
searches.

### Engaging the servo

With the operating point centered on the slope: switch the laser drive
from ramp to DC on the function generator, then enable the Rb SIM960 in
PID mode. The lock should pull the laser onto the reference slope and
hold, provided PPLN temperature, optical power, and gains are in range.

### The old auto-lock algorithm (reference)

The previous system automated the feature search with convolution-based
pattern matching; the reference traces live in `data/` in this
repository:

1. Pre-recorded Rb transmission traces exist for each scan range of
   interest.
2. The current oscilloscope trace is convolved with the stored
   template; the convolution maximum locates the best alignment.
3. The scan range is then narrowed and the process repeated, zooming in
   on the target sub-feature each time.
4. When the sweep covers a single monotonic slope, the corresponding
   laser control voltage becomes the servo offset and PID mode is
   engaged.

```{figure} images/rb1.png
:width: 70%

Rb transmission and PDH signal, full scan range.
```

```{figure} images/rb2.png
:width: 70%

The same signals over a narrowed scan range.
```

```{figure} images/rb_conv1.png
:width: 70%

Convolution of the current trace with the stored template; the red
point marks the best match.
```

```{figure} images/rb3and_target.png
:width: 70%

Final narrow sweep with the target slope highlighted.
```

This staged narrowing is robust against moderate drifts in temperature
and optical power.

### Periodic lock checks

The Rb lock underpins the comb's frequency stability and should be
checked periodically during observations:

- apply a small, narrow-range dither to the laser control;
- watch the Rb servo output and error signal;
- a healthy engaged lock shows a small servo response at the dither
  frequency with **opposite sign** to the error signal (negative
  feedback).

```{figure} images/lock_check.png
:width: 70%

Servo output time series under a small laser-voltage perturbation —
the signature of an engaged lock.
```

If the servo stops responding this way, or the PDH signal degrades, the
lock is lost or drifting: re-run the locking procedure.

## General power-up discipline

From the original manual's closing advice: follow the power-up and
shutdown orders given in the {doc}`operational notes
<../instruments/operations>`, and keep safety margins on all
high-power devices — especially the RF amplifier and the Pritel main
amplifier.
