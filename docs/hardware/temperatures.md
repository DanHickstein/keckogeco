# Temperature log by operating condition

Equilibrated thermocouple readings from the two USB-2408 DAQ boards
(channel map as in the GUI's Temperatures panel /
`drivers/usb2408.DEFAULT_POSITIONS`), recorded under each operating
condition as it changes during commissioning. The GUI's red/blue
coloring compares against one fixed per-channel baseline (the
2026-07-14 row); this table is where the other conditions live.

The rack board's ch7 is permanently unconnected and left out. The
laptop CPU column is the control laptop's ACPI package temperature
(GUI Laptop tab).

## Instrument rack (DAQ 205F843) + laptop

All values °C.

| Date | Condition | Side baffle (0) | WaveShaper (1) | Rb clock (2) | Pritel (3) | Glycol out (4) | Glycol in (5) | PSU shelf (6) | Laptop CPU |
|---|---|---|---|---|---|---|---|---|---|
| 2026-07-14 | Rack door **open**, Pritel off [^laptop-loc] | 28.7 | 26.4 | 27.0 | 26.0 | 19.6 | 14.1 | 26.0 | ~75 |
| 2026-07-18 | Rack door **closed** (overnight), Pritel off, EDFA27 on | 34.3 | 34.3 | 34.3 | 32.3 | 26.1 | 17.0 | 33.7 | 84 |
| 2026-07-19 | Rack door closed, EDFA27 + Pritel **on** (overnight — the operating condition) | 35.4 | 36.5 | 35.3 | 34.0 | 26.7 | 17.5 | 34.3 | 91 |

[^laptop-loc]: The laptop sat outside the rack for this row.

## Optical table (EOCB, DAQ 205F82F)

All values °C.

| Date | Condition | RF osc (0) | RF amp (1) | Phase mods (2) | Filter cavity (3) | Glycol in (4) [^glycol-swap] | Glycol out (5) | Compression (6) | Rb cell (7) |
|---|---|---|---|---|---|---|---|---|---|
| 2026-07-14 | Rack door open, Pritel off | 40.3 | 48.0 | 32.7 | 28.0 | 15.6 | 34.9 | 23.1 | 24.2 |
| 2026-07-18 | Rack door closed (overnight), Pritel off, EDFA27 on | 40.6 | 48.5 | 33.1 | 28.3 | 15.8 | 34.7 | 23.7 | 24.5 |
| 2026-07-19 | Rack door closed, EDFA27 + Pritel on (overnight — the operating condition) | 40.8 | 48.4 | 33.3 | 28.5 | 15.9 | 34.2 | 24.3 | 24.8 |

[^glycol-swap]: ch4/ch5 in–out labels corrected 2026-07-18 — the
    June-2023 channel doc had them reversed (the "inlet" read ~35 °C,
    the "outlet" the ~15 °C facility supply). Values in all rows are
    per-channel and unaffected. See `ktl/keyword-changes.md`.

## Notes

- The ~19 °C glycol rise across the optical table (in ~15.6 →
  out ~34.7 °C, vs the rack loop's ~5 °C) is expected, not a fault
  (Dan, 2026-07-18): the RF amplifier's heat load is large for a
  breadboard cooling loop, and the ~1/4" copper tubing weaving through
  the board limits flow. Watch for *changes* against these baselines:
  glycol-out creeping up at constant load suggests flow degrading;
  component channels rising while glycol-out drops suggests thermal
  contact failing.

- **The 2026-07-19 row is the GUI baseline set**
  (`gui/mainwindow._THERMO_PANELS`) — the operating condition, per Dan.
  Each channel's GUI tooltip carries all three recorded conditions
  ("open / closed / +Pritel"). The 2026-07-14 row was the original
  baseline set (averaged over five `/keywords` snapshots).
- To capture a row: let the system sit in the condition for several
  hours (overnight is best), then read `LFC_TEMP_TEST1` (rack) and
  `LFC_TEMP_TEST2` (table) from `GET /api/v1/keywords`, or copy the
  GUI's Temperatures panel.
- If a new condition becomes the normal operating state, update the
  GUI baselines in `_THERMO_PANELS` to match, so the red/blue coloring
  keeps meaning "abnormal for how we run".
