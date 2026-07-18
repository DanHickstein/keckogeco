# Hardware

The physical astrocomb: facility installation, the component inventory,
and the detailed instrument design. Adapted from chapter 5 of the
original operations manual (Caltech / Aerospace Corporation).

```{toctree}
:maxdepth: 1

components
design
temperatures
```

## Facility

The NIR astrocomb assembly is located in the basement of the Keck II
telescope and occupies an optical bench and one electronics rack. The
interfaces (optical, mechanical, electrical, thermal, and
communications) between the LFC and the facility are detailed in the
NIR LFC Interface Control Document (ICD).

The assembly is composed of:

- an **instrument rack** (24" W × 38" D × 81" H);
- two 18" × 24" **glycol-cooled optical breadboards** fixed to a 3'-wide
  optical bench, housed inside a 24" × 36" × 12" optical enclosure with
  a top-hinged lid;
- a self-contained **spectral flattener unit** resting on the optical
  bench adjacent to the breadboard enclosure.

Adjacent to the electronics rack, on the side opposite the optical
bench, a ceiling-mounted **facility interconnect panel** provides clean
power, glycol coolant, facility air, and ethernet. Beneath it on the
floor are two 15 A **UPS units** for smooth power handling during
facility outages. The rack abuts the 3' end of the optical bench, and a
15-inch interconnect panel on its side connects bench components to the
rack instruments via optical ports (FC/APC), RF ports (SMA), USB,
ethernet, and electrical ports.

Electrical power runs from the top of the rack across a ceiling cable
tray to the interface panel. Glycol lines run from a manifold behind
the interface panel down the side of the bench into the rack, and up
the opposite side to the breadboards and a component cooling block.
The output of the spectral flattener is an optical fiber carrying the
comb light to the observatory dome.

The rack houses the large electronics: three erbium-doped fiber
amplifiers, the rubidium clock for comb frequency stabilization, the
dispersion-compensation unit (WaveShaper), the RF oscillator and RF
amplifier power supplies, servo controllers, the frequency counter, the
control laptop, an optical spectrum analyzer, and web-controlled power
distribution units for remote power control of each instrument.

```{figure} images/image.png
:width: 95%

Detailed NIR LFC signal chain.
```

```{figure} images/image2.jpg
:width: 95%

Overall view of the LFC setup.
```

```{figure} images/interface_panel.jpg
:width: 70%

The facility interface panel (power, glycol, air, ethernet).
```

```{note}
The rack's internal network addresses (router table, MAC/IP
assignments) are deliberately not published here — they live in the
private operations notes alongside the git-ignored configuration.
```
