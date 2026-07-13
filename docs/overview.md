# What is the astrocomb?

```{note}
This page is adapted from the introduction of the original LFC operations
manual (Caltech / Aerospace Corporation).
```

An **astrocomb** is a laser frequency comb (LFC) calibration source for
astronomical spectrographs. Like gas cells, arc lamps, and etalons, it
provides light at well-defined frequencies to serve as a "spectral ruler" —
something to compare against target-object spectral signatures, and a way to
monitor shifts in spectrograph pixel positions caused by thermo-mechanical
changes in the instrument. That makes it an essential tool for precision
radial-velocity studies of exoplanet-hosting stars.

Unlike gas cells and arc lamps, an LFC provides light "markers" at very
uniform spacing, optimized for a particular spectrograph's resolution. And
unlike etalons, the spacing between the lines is traceable to a
GPS-disciplined RF signal. Astrocombs are therefore the highest-precision,
highest-accuracy calibration sources available to astronomical spectrographs.
They are also the most complex of these instruments.

This comb is an electro-optic (EO) near-infrared (NIR) LFC designed and
built by the California Institute of Technology and the Aerospace
Corporation for the W. M. Keck Observatory, under a grant from the
Heising-Simons Foundation. It is intended for use initially with the
NIRSPEC spectrograph, and eventually with the HISPEC spectrograph. Its
light is injected through the Keck Planet Imager and Characterizer (KPIC)
fiber injection unit, though it can also be coupled directly to a
spectrograph integrating sphere. The assembly is largely composed of
commercial off-the-shelf (COTS) parts developed for the optical
telecommunications industry, so spare parts are generally available.

## Operating principle

An electro-optic LFC generates its comb of calibration lines by creating
sidebands on a continuous-wave (CW) laser through electro-optic modulation
at a prescribed frequency:

1. An **EO intensity modulator** carves pulses out of the CW pump at a
   specific radio frequency (16 GHz here).
2. **Cascaded EO phase modulators** temporally chirp the pulses, adding
   optical bandwidth. The result is a *minicomb* whose bandwidth and line
   count are governed by the number of cascaded phase modulators and the
   RF drive voltage — about 11 nm when optimized.
3. The frequencies of both the pump laser and the RF drive oscillator are
   **locked to stable references**, providing the comb stability needed
   for spectrograph calibration.
4. The minicomb is **temporally compressed** and then **spectrally
   broadened** (in nonlinear fiber and a waveguide) to cover the desired
   spectrograph bandpass.
5. A **spectral flattener** equalizes the line intensities, and the full,
   flattened comb is **attenuated** to a level appropriate for the desired
   integration time.
6. A **shutter** isolates the LFC from the observatory instruments, which
   are fed by optical fiber.

## Key performance parameters

| Parameter | Value | Notes |
|---|---|---|
| Mode spacing | 16 GHz | Set by the drive oscillator, fixed for this assembly; the hardware is capable of 12–18 GHz line spacing. |
| Minimum wavelength | 1500 nm | The comb produces light well blue of 1500 nm, but the spectral flattener operates between 1500 and 2500 nm. |
| Maximum wavelength | 2500 nm | Comb light extends to 2500 nm, with lower intensity and SNR at the red end of the bandpass. |
| Dynamic range | 60 dB | Achieved in ~10 dB steps using neutral-density filters. |
| Frequency stability | 10⁻¹⁰ | Depending on the pump reference. |

## How these docs are organized

- {doc}`user_guide/index` — operating the comb: installation, the
  engineering GUI, the Menlo flattener, and running the comb from the
  Keck side.
- {doc}`instruments/index` — the rack instruments: what each one does and
  its operational quirks.
- {doc}`keck/index` — the KTL keyword service: keyword reference, deploy
  procedure, and the change list.
- {doc}`api/index` — Python API reference for the `keckogeco` package.
- {doc}`development` — contributing to the software.
