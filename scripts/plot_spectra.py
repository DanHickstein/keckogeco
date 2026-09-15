"""Plot saved OSA spectra (CSV files from the GUIs) and optionally compute
fiber attenuation from a pair of them.

Run with no arguments to reproduce the 2026-07-22 ZBLAN comparison:
three supercontinuum spectra plus the 11.6 m ZBLAN attenuation curve.

General use::

    python scripts/plot_spectra.py FILE [FILE ...]
        [--labels LABEL [LABEL ...]]
        [--attenuation REF MEAS LENGTH_M]   # 1-based spectrum indices
        [--save out.png]

``--attenuation 2 3 11.6`` means: spectrum 3 is spectrum 2 after LENGTH_M
metres of extra fiber, so attenuation = (P2 - P3) / 11.6 in dB/m.  Points
where either trace sits near its own noise floor are masked out.
"""

import argparse
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

REPO_ROOT = Path(__file__).resolve().parent.parent
SPECTRA_DIR = REPO_ROOT / "spectra"

# Default dataset: the 2026-07-22 Zach-waveguide supercontinuum + ZBLAN session.
DEFAULT_FILES = [
    SPECTRA_DIR / "yokogawa_2026-07-22_125010 - zach waveguide at 4.2A, 4.07W.csv",
    SPECTRA_DIR / "yokogawa_2026-07-22_130223 - Zach 4.2A with 0.5m ZBLAN.csv",
    SPECTRA_DIR / "yokogawa_2026-07-22_140332 - zach waveguide 0.5 + 0.5 meters ZBLAN.csv",
    SPECTRA_DIR / "yokogawa_2026-07-22_132840 - Zach waveguide - 0.5 m + 11.6 m ZBLAN.csv",
    SPECTRA_DIR / "yokogawa_2026-07-22_131821 - Zach waveguide + 0.5 + 11.6 meters ZBLAN.csv",
]
DEFAULT_LABELS = [
    "Supercontinuum",
    "Supercontinuum + 0.5 m ZBLAN",
    "Supercontinuum + 0.5 m + 0.5 m ZBLAN",
    "Supercontinuum + 0.5 m + 11.6 m ZBLAN (SN 11/12)",
    "Supercontinuum + 0.5 m + 11.6 m ZBLAN (SN 21/22)",
]
DEFAULT_DASHED = [5]  # the SN 21/22 fiber never coupled well - shown for reference only
DEFAULT_ATTENUATION = (2, 4, 11.6)  # (P2 - P4) / 11.6 m -> ZBLAN dB/m, from the good SN 11/12 fiber
DEFAULT_COUPLING_LOSS_DB = 0.5  # assumed lumped loss at the 0.5 m patch <-> spool connection
DEFAULT_FADE_BELOW_NM = 1700  # pump is highly modulated below here - low confidence in the loss

# Categorical series colors (validated palette; fixed assignment order).
SERIES_COLORS = ["#2a78d6", "#008300", "#e87ba4", "#eda100", "#1baf7a", "#eb6834"]
ATTEN_COLOR = "#4a3aa7"
GRID_COLOR = "#e1e0d9"


def load_spectrum(path):
    """Load one saved OSA spectrum CSV.

    Parameters
    ----------
    path : Path
        CSV with ``# key: value`` header comments and
        ``wavelength_nm,power_dBm`` columns.

    Returns
    -------
    wavelength_nm : ndarray
    power_dBm : ndarray
    meta : dict
        Header key/value pairs (all strings).
    """
    meta = {}
    n_header = 0
    with open(path) as f:
        for line in f:
            n_header += 1
            if not line.startswith("#"):
                break  # the wavelength_nm,power_dBm column-name line
            if ":" in line:
                key, _, value = line.lstrip("# ").partition(":")
                meta[key.strip()] = value.strip()
    wl, p = np.loadtxt(path, delimiter=",", skiprows=n_header, unpack=True)
    return wl, p, meta


def attenuation_dB_per_m(
    wl_ref, p_ref, wl_meas, p_meas, length_m, floor_margin_dB=5.0, coupling_loss_dB=0.0
):
    """Spectral attenuation between two spectra separated by ``length_m`` of fiber.

    Returns
    -------
    wl : ndarray
        Reference wavelength grid.
    atten : masked ndarray
        (P_ref - P_meas - coupling_loss_dB) / length_m in dB/m, masked where
        either trace is within ``floor_margin_dB`` of its own noise floor
        (estimated as the 5th percentile of the trace).
    """
    p_meas_i = np.interp(wl_ref, wl_meas, p_meas)
    usable = (p_ref > np.percentile(p_ref, 5) + floor_margin_dB) & (
        p_meas_i > np.percentile(p_meas, 5) + floor_margin_dB
    )
    atten = np.ma.masked_where(~usable, (p_ref - p_meas_i - coupling_loss_dB) / length_m)
    return wl_ref, atten


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "files", nargs="*", type=Path, help="spectrum CSVs (default: 2026-07-22 ZBLAN set)"
    )
    parser.add_argument("--labels", nargs="*", default=None, help="legend label per file")
    parser.add_argument(
        "--attenuation",
        nargs=3,
        type=float,
        metavar=("REF", "MEAS", "LENGTH_M"),
        default=None,
        help="1-based indices of the reference and measured spectra, and the fiber length in metres",
    )
    parser.add_argument(
        "--floor-margin",
        type=float,
        default=5.0,
        help="dB above noise floor required to trust a point (default 5)",
    )
    parser.add_argument(
        "--coupling-loss",
        type=float,
        default=None,
        help="lumped connection loss in dB to subtract before dividing by fiber length "
        f"(default {DEFAULT_COUPLING_LOSS_DB} for the built-in dataset, else 0)",
    )
    parser.add_argument(
        "--dashed",
        nargs="*",
        type=int,
        default=None,
        help="1-based indices of spectra to draw dashed",
    )
    parser.add_argument(
        "--fade-below",
        type=float,
        default=None,
        help="draw the attenuation curve faded below this wavelength (nm) to mark low "
        f"confidence (default {DEFAULT_FADE_BELOW_NM} for the built-in dataset, else off)",
    )
    parser.add_argument("--save", type=Path, default=None, help="also save the figure to this file")
    args = parser.parse_args()

    files = args.files or DEFAULT_FILES
    labels = (
        args.labels
        if args.labels
        else (DEFAULT_LABELS if not args.files else [p.stem for p in files])
    )
    attenuation = args.attenuation or (DEFAULT_ATTENUATION if not args.files else None)
    dashed = args.dashed if args.dashed is not None else (DEFAULT_DASHED if not args.files else [])
    coupling_loss = args.coupling_loss
    if coupling_loss is None:
        coupling_loss = DEFAULT_COUPLING_LOSS_DB if not args.files else 0.0
    fade_below = args.fade_below
    if fade_below is None and not args.files:
        fade_below = DEFAULT_FADE_BELOW_NM
    if len(labels) != len(files):
        parser.error(f"{len(files)} files but {len(labels)} labels")

    spectra = [load_spectrum(p) for p in files]
    for path, (wl, p, _meta) in zip(files, spectra, strict=True):
        print(f"{path.name}: {wl[0]:.0f}-{wl[-1]:.0f} nm, {len(wl)} pts, peak {p.max():.1f} dBm")

    if attenuation is not None:
        fig, (ax, ax_att) = plt.subplots(2, 1, sharex=True, figsize=(10, 7), height_ratios=[2, 1])
    else:
        fig, ax = plt.subplots(figsize=(10, 5))

    for i, ((wl, p, _meta), label, color) in enumerate(
        zip(spectra, labels, SERIES_COLORS, strict=False), start=1
    ):
        linestyle = "--" if i in dashed else "-"
        ax.plot(wl, p, label=label, color=color, linewidth=1.5, linestyle=linestyle)
    ax.set_ylabel("Power (dBm)")
    ax.legend()
    ax.grid(color=GRID_COLOR)

    if attenuation is not None:
        i_ref, i_meas, length_m = int(attenuation[0]) - 1, int(attenuation[1]) - 1, attenuation[2]
        wl, atten = attenuation_dB_per_m(
            spectra[i_ref][0],
            spectra[i_ref][1],
            spectra[i_meas][0],
            spectra[i_meas][1],
            length_m,
            args.floor_margin,
            coupling_loss,
        )
        if fade_below is not None:
            lo, hi = wl <= fade_below, wl >= fade_below  # overlap one sample for continuity
            ax_att.plot(wl[lo], atten[lo], color=ATTEN_COLOR, linewidth=1.5, alpha=0.4)
            ax_att.plot(wl[hi], atten[hi], color=ATTEN_COLOR, linewidth=1.5)
        else:
            ax_att.plot(wl, atten, color=ATTEN_COLOR, linewidth=1.5)
        ax_att.set_ylabel(f"Attenuation (dB/m)\nover {length_m:g} m")
        ax_att.set_xlabel("Wavelength (nm)")
        ax_att.grid(color=GRID_COLOR)
        ax_att.axhline(0, color="#c3c2b7", linewidth=0.8)

        if atten.count():
            wl_ok = wl[~atten.mask]
            i_min = np.ma.argmin(atten)
            print(
                f"\nAttenuation of {length_m:g} m of fiber "
                f"({labels[i_meas]!r} vs {labels[i_ref]!r}, "
                f"{coupling_loss:g} dB coupling loss removed):\n"
                f"  usable band : {wl_ok[0]:.0f}-{wl_ok[-1]:.0f} nm "
                f"({atten.count()} of {len(wl)} points above noise floor)\n"
                f"  mean        : {atten.mean():.3f} dB/m\n"
                f"  minimum     : {atten.min():.3f} dB/m at {wl[i_min]:.0f} nm"
            )
            if fade_below is not None:
                confident = atten[wl >= fade_below]
                if confident.count():
                    print(
                        f"  >= {fade_below:g} nm  : mean {confident.mean():.3f} dB/m, "
                        f"min {confident.min():.3f} dB/m "
                        f"(below {fade_below:g} nm the pump structure makes the "
                        "curve low-confidence; drawn faded)"
                    )
        else:
            print("\nNo overlap above the noise floor - no attenuation computed.")
    else:
        ax.set_xlabel("Wavelength (nm)")

    fig.tight_layout()
    if args.save:
        fig.savefig(args.save, dpi=150)
        print(f"\nSaved figure to {args.save}")
    plt.show()


if __name__ == "__main__":
    main()
