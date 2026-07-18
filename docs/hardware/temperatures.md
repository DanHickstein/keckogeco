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
| 2026-07-18 | Rack door **closed** (overnight), Pritel off | — [^pending] | — | — | — | — | — | — | 84 |
| _(planned)_ | Rack door closed, EDFA27 + Pritel **on** — record tonight after equilibration | | | | | | | | |

[^laptop-loc]: The laptop sat outside the rack for this row.
[^pending]: Server was down at recording time (wedged since 2026-07-17
    18:00, see the log); fill in after restart while the door-closed /
    Pritel-off condition still holds.

## Optical table (EOCB, DAQ 205F82F)

All values °C.

| Date | Condition | RF osc (0) | RF amp (1) | Phase mods (2) | Filter cavity (3) | Glycol out (4) | Glycol in (5) | Compression (6) | Rb cell (7) |
|---|---|---|---|---|---|---|---|---|---|
| 2026-07-14 | Rack door open, Pritel off | 40.3 | 48.0 | 32.7 | 28.0 | 15.6 | 34.9 | 23.1 | 24.2 |
| 2026-07-18 | Rack door closed (overnight), Pritel off | — | — | — | — | — | — | — | — |
| _(planned)_ | Rack door closed, EDFA27 + Pritel on | | | | | | | | |

## Notes

- The 2026-07-14 row is the GUI baseline set
  (`gui/mainwindow._THERMO_PANELS`), recorded on the live rack with the
  system in its normal state and averaged over five `/keywords`
  snapshots.
- To capture a row: let the system sit in the condition for several
  hours (overnight is best), then read `LFC_TEMP_TEST1` (rack) and
  `LFC_TEMP_TEST2` (table) from `GET /api/v1/keywords`, or copy the
  GUI's Temperatures panel.
- If a new condition becomes the normal operating state, update the
  GUI baselines in `_THERMO_PANELS` to match, so the red/blue coloring
  keeps meaning "abnormal for how we run".
