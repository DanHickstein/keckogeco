# Installation on a new computer

Setting up a fresh Windows machine as an LFC control station, from bare OS
to a running server and GUI. The order matters: install the vendor driver
stacks before expecting `keckogeco` to see any hardware.

This page modernizes the step-by-step guide from the original KeckLFC
repository (tested on the real control laptops) for the rewritten package.
The big differences: plain `pip` instead of Anaconda, and the WaveShaper
setup no longer needs admin rights or DLL copies into `System32`.

```{note}
Everything here targets Windows 10/11, since only the Windows control
laptop talks to the hardware. On macOS/Linux/CI, `pip install -e ".[dev]"`
is all you need — the package imports cleanly without any vendor
libraries, and the whole test suite plus `--sim` mode run without them.
```

## 1. Python

Install Python **3.11 or newer** from [python.org](https://www.python.org/downloads/)
(the deployed laptop, LAPTOP-LFC2, runs 3.13). Anaconda is not needed.

- During installation, tick *"Add python.exe to PATH"*.
- Admin rights are **not** required: a per-user install plus `pip install --user`
  works fine. One consequence of user-site installs is that console scripts
  land off `PATH`, which is why every keckogeco entry point is invoked as
  `python -m ...` (or via the editor's Run button) rather than as an `.exe`.

## 2. Get keckogeco

```powershell
git clone https://github.com/danhickstein/keckogeco
cd keckogeco
pip install -e ".[gui]"
```

Extras: `gui` (PyQt6 + pyqtgraph, for the engineering GUI), `daq`
(mcculw, for the USB-2408 thermocouple DAQ), `dev` and `docs` for
development work. On the control laptop you want at least `".[gui,daq]"`.

Instrument addresses live in a git-ignored config file — see
`config/README.md`. Don't write it by hand; discovery generates it
(step 7).

## 3. VISA and GPIB: NI-VISA, NI-488.2, NI-MAX

`pyvisa` needs a VISA implementation, and the GPIB instruments (SRS SIM900,
Pendulum counter, Agilent OSA) need the NI GPIB driver:

1. **[NI-VISA](https://www.ni.com/en/support/downloads/drivers/download.ni-visa.html)** —
   the VISA layer itself (serial + USB-TMC + GPIB resources).
2. **[NI-488.2](https://www.ni.com/en/support/downloads/drivers/download.ni-488-2.html)** —
   GPIB support.
3. **NI-MAX** comes with the above and is the go-to debugging tool: if an
   instrument doesn't appear in `python -m keckogeco.discovery`, check
   whether NI-MAX sees it first.

```{warning}
The rack's GPIB interface is known to break with the newest NI-488.2
releases — the deployed laptop runs a deliberately **downgraded**
NI-488.2. If `discovery` reports a GPIB driver-stack error while NI-MAX
shows the board, downgrade NI-488.2.
<!-- TODO: record the exact working NI-488.2 version once confirmed on
LAPTOP-LFC2 (tracked in GitHub issue #1). -->
```

A free NI user account may be required for the downloads.

## 4. Keysight IO Libraries Suite

Used for the USB-TMC instruments (Keysight 33500-series function
generators and friends). Install from
[keysight.com](https://www.keysight.com/us/en/lib/software-detail/computer-software/io-libraries-suite-downloads-2175637.html)
with default options. When the installer asks about the primary VISA,
keep NI-VISA primary and let Keysight install side-by-side.

## 5. Instek PSU USB driver

The GW Instek supplies (GPD-4303S for the RF oscillator, GPP-1326 for the
RF amplifier) present as USB serial ports once their driver is installed.
Download the *USB driver* from the
[GPP series download page](https://www.gwinstek.com/en-IN/products/detail/GPP-Series)
and install with defaults.

## 6. MCC InstaCal (USB-2408 DAQ)

The USB-2408 thermocouple DAQ is driven through `mcculw` (the `daq`
extra), which requires MCC's **InstaCal** to be installed and the board
to have been opened in InstaCal once (that assigns the board number the
config refers to). Download from the
[MCC / Digilent site](https://digilent.com/reference/software/instacal/start).

## 7. WaveShaper software and `wsapi`

The Finisar/II-VI WaveShapers need the most involved setup. **All three**
applications must be installed (a classic mistake is installing only one),
followed by a first run of WaveManager, followed by the Python package:

1. Install, with default paths, from the II-VI/Coherent instrumentation
   downloads:
   - **WaveAnalyzer GUI Software**
   - **WaveManager**
   - **WaveShaper App**

   Afterwards, `C:\Program Files (x86)\Finisar\WaveManager\waveshaper`
   must exist — if it doesn't, one of the three is missing.

2. **Run WaveManager once with the WaveShapers connected** and let it
   detect them. This first run saves each unit's settings/configuration
   files to disk — `wsapi` reads those files when opening a device by
   serial number, and **cannot open the device until they exist**.

3. Install the `wsapi` Python package. Its source ships inside the
   WaveManager install, but `pip` cannot build it in place under
   `Program Files` without admin rights — copy it somewhere writable
   first:

   ```powershell
   xcopy /E /I "C:\Program Files (x86)\Finisar\WaveManager\waveshaper\api\python3" C:\kecklfc\wsapi-src
   cd C:\kecklfc\wsapi-src
   pip install .
   ```

4. Verify:

   ```powershell
   python -c "from wsapi import ws_get_version; print(ws_get_version())"
   ```

   This should print a version like `2.7.5`. If it returns `-1`, the DLLs
   didn't load.

```{note}
The vendor instructions tell you to copy `wsapi.dll`, `ftd2xx64.dll`, and
`ws_cheetah64.dll` into `C:\Windows\System32` (admin required). **Skip
that** — the keckogeco waveshaper driver registers the WaveManager DLL
directory itself via `os.add_dll_directory` before importing `wsapi`.
```

## 8. Arduino IDE (optional)

The Arduino board implements the latched interlock relay protecting the
Pritel amplifier against Q-switch damage on seed loss. The
[Arduino IDE](https://www.arduino.cc/en/software) is only needed to
*modify* that firmware; normal operation just talks to the board over its
serial port, which needs no extra software.

## 9. First run

With the vendor stacks in place and the instruments connected:

```powershell
python -m keckogeco.discovery      # probe every port, write the config
python -m keckogeco.check          # try connecting to each configured device
```

Discovery writes `[devices.*]` blocks to the config file, anchored to USB
adapter serial numbers so they survive COM-port renumbering. Two manual
touches afterwards:

- Rename the generated device keys to the canonical names the controller
  binds keywords to (`edfa27`, `rf_osc_psu`, `switch2x2`, `clarity`, ... —
  `config/instruments.example.toml` is the reference).
- Review any blocks written with `enabled = false` (unidentified devices,
  or drivers not yet ported).

Then start the stack:

```powershell
python -m keckogeco.server.app     # owns the hardware; runs 24/7
python -m keckogeco.gui.app        # engineering GUI (pure REST client)
```

All four entry points also run as plain files — open
`keckogeco/discovery.py`, `keckogeco/check.py`, `keckogeco/server/app.py`,
or `keckogeco/gui/app.py` in VSCode and press Run.

## Verification checklist

| Check | Command / expectation |
|---|---|
| Python | `python --version` → 3.11+ |
| Package | `python -c "import keckogeco"` → no error |
| VISA | NI-MAX lists the connected instruments |
| GPIB | `python -m keckogeco.discovery` scans GPIB without a driver-stack error |
| wsapi | `python -c "from wsapi import ws_get_version; print(ws_get_version())"` → version string |
| DAQ | board visible in InstaCal |
| End-to-end | `python -m keckogeco.check` → `ok` per enabled device |
| No hardware | `python -m keckogeco.server.app --sim` + the GUI open and populate |

## Troubleshooting

- **An instrument is missing from discovery** — check NI-MAX (VISA
  devices) or Device Manager (plain COM ports) first; if the OS can't see
  it, no Python will.
- **GPIB scan fails with a driver-stack error** — NI-488.2 version problem;
  see the downgrade warning in step 3.
- **`wsapi` imports but opening `SN<serial>` fails** even though the USB
  device is present — WaveManager has never been run on this machine; do
  step 7.2.
- **`keckogeco-server` / `keckogeco-gui` not found** — there are no
  console-script executables; use `python -m keckogeco.server.app` and
  `python -m keckogeco.gui.app`.
- **A device fails in `check` with a timeout** — confirm it's powered and
  that nothing else (vendor GUI, old software) holds its COM port open;
  ports are exclusive.
