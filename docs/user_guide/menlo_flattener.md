# Menlo flattener and output attenuator

The spectral flattener and the output attenuation slider run on the
**Menlo laptop** — an isolated computer, separate from the LFC control
laptop, that hosts the Menlo flattener and slider control software
locally. Nothing about it is controlled through `keckogeco`; it is
operated by remote desktop, exactly as it was under the original control
system.

The typical roles in a full-comb session:

1. Bring the comb to FULL COMB from the control side (GUI or Keck
   keywords — see {doc}`keck_operations`).
2. Use the Menlo software to monitor the spectrum and flatten (or
   filter) it.
3. Use the slider to attenuate the output to a level suitable for the
   observation.

## Remote access via Google Remote Desktop

Because the Menlo laptop is isolated, it is accessed remotely from the
LFC laptop:

1. Log in to the LFC laptop.
2. Open a web browser and go to Google Remote Desktop (there is an icon,
   or search for "Google Remote Desktop").
3. In the list of available machines, click the entry for the
   **Menlo flattener** to start a remote session.

```{figure} images/Googleremote1.png
:width: 70%

Google Remote Desktop machine list on the LFC laptop.
```

Once connected you will see the Menlo desktop, which provides:

- **Flattener Control** — flattens the comb spectrum using a spatial
  light modulator (SLM).
- **Slider Control** — attenuates the comb output using a hardware
  slider with discrete positions.

```{figure} images/Googleremote2.png
:width: 70%

The Menlo flattener desktop.
```

## Launching the Flattener Control software

```{figure} images/menlospectrum_start.png
:width: 80%

Launching the Flattener Control software. The numbered controls are
referenced in the steps below.
```

1. Click the icon labeled **1** to launch the Flattener Control.
2. Wait roughly 10 seconds for a simplified control panel to appear.
3. The simplified panel has limited functionality — prefer the full
   panel: click the control labeled **2** to bring it up.
4. Once the full panel shows the blue spectral trace, click buttons
   **3** and **4** in order:
   1. Button **3** launches the spatial light modulator (SLM).
   2. Button **4** launches the optical spectrum analyzer (OSA).

The SLM assigns **one pixel per comb line**, so each SLM pixel
corresponds to a specific comb-line frequency and can attenuate that
line individually. The OSA shows the comb spectrum in real time.

If the spectrum does not update on the OSA:

- repeat steps 1–4 to relaunch the panels, or
- reduce the OSA averaging (set it to 1 or 2).

## Flattener modes: filter *or* flatten

The flattener offers two primary modes. **Only one of them can be used
at a time.**

### Filter mode

```{figure} images/flatten_full_comb1.png
:width: 80%

Filter mode: the gray band marks the comb lines to be attenuated.
```

- Select **Filter** mode with the left-hand button on the flattener
  interface.
- A gray band appears on the SLM display, indicating which comb-line
  frequencies will be attenuated. You can adjust:
  - the **width** of the band (filter bandwidth),
  - the **position** of the band (center frequency),
  - the **gray level** (depth of attenuation).

A typical use is filtering out the pump line.

### Flatten mode

```{figure} images/flatten_full_comb2.png
:width: 80%

Flatten mode: the SLM is adjusted automatically to a target level.
```

- Select **Flatten** mode with the right-hand button.
- Set a target truncation (flattening) level — for example −23, −20,
  −17, −15, −13, or −10 dB.
- Click **Start**; the system automatically adjusts the SLM to flatten
  the comb spectrum to the target level.

### Recommended settings

- OSA averaging: 4, 2, or 1.
- If the spectrum **oscillates** during flattening, reduce the flatten
  gain (e.g. from the default 0.25 to 0.15).
- If the spectrum is not fully displayed, use **rescale Y axis** on the
  Menlo OSA panel.

## Slider control and power attenuation

```{figure} images/slider_start.png
:width: 80%

The ELLO slider control application.
```

To launch the slider control on the Menlo laptop:

1. Open the application named **ELLO**.
2. Connect to the slider device from within ELLO.
3. A window appears showing slider positions and controls.

The slider offers six discrete attenuation settings. Using
**position 6** as the 0 dB reference, the approximate insertion losses
are:

| Position | Attenuation | Measured output (55 mW input) |
|---|---|---|
| 1 | ~5 dB | ~15 µW |
| 2 | ~10 dB | ~4.8 µW |
| 3 | ~20 dB | ~0.5 µW |
| 4 | >20 dB | 0 µW |
| 5 | >20 dB | 0 µW |
| 6 | 0 dB (reference) | ~48 µW |

The slider is typically used **after** the full comb has been brought
up, to attenuate the comb power to a level suitable for the
observation.
