"""Finisar/II-VI WaveShaper programmable optical filter.

Two units on the rack: the minicomb filter and the spectral flattener.
Unlike the serial instruments this device is driven through the vendor
``wsapi`` DLL (Windows-only; see the install notes in the docs), so the
"transport" here is a thin wrapper over the wsapi lifecycle
(create → open → load-profile → close → delete). ``wsapi`` is imported
lazily inside ``open()`` so the package imports cleanly everywhere.

Ported from ``Hardware/Waveshaper.py``, minus the matplotlib previews and
the ``winsound`` beep. The profile model is unchanged: attenuation and
phase are functions of optical frequency (THz), sampled on the device's
1 GHz grid and uploaded as a 4-column text profile.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import ClassVar

import numpy as np

from ..config import DeviceConfig
from .base import Instrument
from .errors import InstrumentError
from .transports import Transport

__all__ = ["Waveshaper", "dispersion_phase", "nm_to_thz", "thz_to_nm"]

C_MS = 299_792_458  # speed of light, m/s

MAX_ATTEN_DB = 60.0  # wsapi treats >=60 dB as "blocked"


def nm_to_thz(nm: float) -> float:
    return C_MS / nm / 1000


def thz_to_nm(thz: float) -> float:
    return C_MS / thz / 1000


def dispersion_phase(
    freq_THz: np.ndarray | float,
    d2_ps_nm: float,
    d3_ps_nm2: float = 0.0,
    center_nm: float = 1560.0,
) -> np.ndarray | float:
    """Spectral phase (rad) for 2nd- and 3rd-order dispersion.

    Same math as the old ``set2ndDisper``/``set3rdDisper``: ``d2`` in
    ps/nm, ``d3`` in ps/nm^2, evaluated at frequency offsets from the
    center wavelength.
    """
    center_THz = nm_to_thz(center_nm)
    beta2 = (C_MS / center_THz) ** 2 / (2 * np.pi * C_MS) * (d2_ps_nm * 1e-3)
    beta3 = C_MS**2 / (4 * np.pi**2 * center_THz**4) * (d3_ps_nm2 * 1e-6)
    omega = (np.asarray(freq_THz) - center_THz) * 2 * np.pi
    return beta2 * omega**2 / 2 + beta3 * omega**3 / 6


class WsapiLink:
    """Transport-shaped wrapper over the vendor wsapi DLL lifecycle."""

    def __init__(self, address: str, config_path: str = ""):
        # address is the unit serial number, e.g. "SN201904"
        self.address = address
        self.config_path = config_path
        self._open = False
        self.start_THz: float | None = None
        self.stop_THz: float | None = None

    @staticmethod
    def _register_dll_directory() -> None:
        """Let Python find wsapi.dll in the WaveManager install.

        The vendor instructions copy the DLLs into System32 (admin-only);
        registering the install's bin directory achieves the same without
        admin rights (Python 3.8+ no longer searches PATH for DLLs).
        """
        import os

        for candidate in (
            r"C:\Program Files (x86)\Finisar\WaveManager\waveshaper\bin\amd64",
            r"C:\Program Files\Finisar\WaveManager\waveshaper\bin\amd64",
        ):
            if os.path.isdir(candidate):
                os.add_dll_directory(candidate)

    def _default_config_path(self) -> str:
        import os

        return os.path.join(
            os.getenv("APPDATA", ""), "WaveManager", "wsconfig", f"{self.address}.wsconfig"
        )

    def open(self) -> None:
        if self._open:
            return
        self._register_dll_directory()
        try:
            import wsapi
        except ImportError as exc:
            raise InstrumentError(
                "The 'wsapi' vendor package is not installed. Install the "
                "WaveManager suite and its Python API (see docs) or run in --sim mode."
            ) from exc
        path = self.config_path or self._default_config_path()
        errcode, _ = wsapi.ws_create_waveshaper(self.address, path)
        if errcode < 0:
            raise InstrumentError(f"ws_create_waveshaper({self.address}) failed: {errcode}")
        errcode = wsapi.ws_open_waveshaper(self.address)
        if errcode < 0:
            wsapi.ws_delete_waveshaper(self.address)
            raise InstrumentError(f"ws_open_waveshaper({self.address}) failed: {errcode}")
        self.start_THz = wsapi.ws_get_startfreq(self.address)
        self.stop_THz = wsapi.ws_get_stopfreq(self.address)
        self._open = True

    def close(self) -> None:
        if not self._open:
            return
        import wsapi

        wsapi.ws_close_waveshaper(self.address)
        wsapi.ws_delete_waveshaper(self.address)
        self._open = False

    @property
    def is_open(self) -> bool:
        return self._open

    def load_profile(self, profile_text: str) -> None:
        import wsapi

        errcode = wsapi.ws_load_profile(self.address, profile_text)
        if errcode < 0:
            raise InstrumentError(f"ws_load_profile({self.address}) failed: {errcode}")

    # Transport protocol stubs (stream I/O is meaningless for this device)
    def write(self, cmd: str) -> None:
        raise NotImplementedError("WaveShaper has no text command channel")

    read = query = write
    write_bytes = read_bytes = read_available = write

    def clear(self) -> None:  # noqa: D102 - protocol no-op
        pass


class SimWsapiLink(WsapiLink):
    """Offline stand-in: fixed frequency range, profiles stored not sent."""

    def __init__(self, address: str = "SIM", config_path: str = ""):
        super().__init__(address, config_path)
        self.loaded_profiles: list[str] = []

    def open(self) -> None:
        self.start_THz, self.stop_THz = 191.25, 196.275
        self._open = True

    def close(self) -> None:
        self._open = False

    def load_profile(self, profile_text: str) -> None:
        self.loaded_profiles.append(profile_text)


class Waveshaper(Instrument):
    """One WaveShaper unit.

    Attenuation and phase are held as callables of frequency in THz
    (``atten(f) -> dB``, ``phase(f) -> rad``) and uploaded together by
    :meth:`write_profile`.
    """

    DRIVER_OPTIONS: ClassVar[tuple[str, ...]] = ("config_path",)

    def __init__(self, transport: Transport, name: str = "", config_path: str = ""):
        super().__init__(transport, name)
        del config_path  # consumed by from_config when building the link
        self.atten: Callable[[float], float] = lambda f: 0.0
        self.phase: Callable[[float], float] = lambda f: 0.0
        self.atten_description = ""
        self.phase_description = ""

    @classmethod
    def from_config(cls, cfg: DeviceConfig, sim: bool = False) -> Waveshaper:
        config_path = str(cfg.options.get("config_path", ""))
        link = SimWsapiLink(cfg.address) if sim else WsapiLink(cfg.address, config_path)
        return cls(link, cfg.key)

    def _configure(self) -> None:
        link: WsapiLink = self.transport  # type: ignore[assignment]
        self.start_THz = float(link.start_THz)
        self.stop_THz = float(link.stop_THz)
        self.freq_THz = np.arange(self.start_THz, self.stop_THz, 0.001)

    # ------------------------------------------------------------- profiles

    def set_bandpass(self, center: float, span: float, unit: str = "thz") -> None:
        """Pass [center-span/2, center+span/2], block everything else."""
        if unit.casefold() == "nm":
            start = nm_to_thz(center + span / 2)
            stop = nm_to_thz(center - span / 2)
        elif unit.casefold() == "thz":
            start, stop = center - span / 2, center + span / 2
        else:
            raise ValueError(f"unit must be 'thz' or 'nm', got {unit!r}")
        self.atten = lambda f: 0.0 if start < f < stop else MAX_ATTEN_DB
        self.atten_description = (
            f"bandpass [{start:.3f}, {stop:.3f}] THz "
            f"([{thz_to_nm(stop):.3f}, {thz_to_nm(start):.3f}] nm)"
        )
        self.log.info("%s: %s", self.name, self.atten_description)

    def set_dispersion(self, d2_ps_nm: float, d3_ps_nm2: float = 0.0, center_nm: float = 1560.0):
        """Program 2nd (+ optional 3rd) order dispersion compensation."""
        self.phase = lambda f: float(dispersion_phase(f, d2_ps_nm, d3_ps_nm2, center_nm))
        self.phase_description = (
            f"dispersion d2={d2_ps_nm} ps/nm, d3={d3_ps_nm2} ps/nm^2, center {center_nm} nm"
        )
        self.log.info("%s: %s", self.name, self.phase_description)

    def set_interpolated_phase(self, freq_THz: np.ndarray, phase_rad: np.ndarray) -> None:
        """Phase profile from sampled points (cubic interpolation)."""
        from scipy.interpolate import interp1d

        self.phase = interp1d(freq_THz, phase_rad, kind="cubic", fill_value="extrapolate")
        self.phase_description = "interpolated phase profile"

    def flatten_from_spectrum(
        self,
        osa_wl_nm: np.ndarray,
        osa_power_dBm: np.ndarray,
        max_atten_dB: float = 5.0,
        noise_floor_dBm: float = -30.0,
        comb_center_nm: float = 1560.0,
        comb_fsr_GHz: float = 16.0,
        bandpass_center_THz: float | None = None,
        bandpass_span_THz: float | None = None,
        peak_search: bool = True,
    ) -> tuple[np.ndarray, np.ndarray]:
        """Build an inverse-attenuation (flattening) profile from an OSA trace.

        Port of the old ``inverseAtten``: find the comb lines, attenuate
        each proportionally to its excess power so the output envelope is
        flat, optionally windowed by a bandpass. Returns the peak
        wavelengths/powers used so callers (GUI) can plot them.
        """
        osa_wl_nm = np.asarray(osa_wl_nm).flatten()
        osa_power_dBm = np.asarray(osa_power_dBm).flatten()
        if peak_search:
            from scipy.signal import find_peaks

            fsr_nm = comb_center_nm - 1 / (1 / comb_center_nm + comb_fsr_GHz / C_MS)
            min_distance = fsr_nm / np.mean(np.diff(osa_wl_nm))
            peak_idx, _ = find_peaks(
                osa_power_dBm, height=noise_floor_dBm, distance=0.9 * min_distance
            )
            peak_wl, peak_pw = osa_wl_nm[peak_idx], osa_power_dBm[peak_idx]
        else:
            mask = osa_power_dBm > noise_floor_dBm
            peak_wl, peak_pw = osa_wl_nm[mask], osa_power_dBm[mask]
        if peak_wl.size == 0:
            raise ValueError(
                f"{self.name}: spectrum is empty above {noise_floor_dBm} dBm; lower noise_floor_dBm"
            )
        from scipy.interpolate import interp1d

        peak_freq = nm_to_thz(peak_wl)
        atten_interp = interp1d(
            peak_freq,
            peak_pw - peak_pw.max() + max_atten_dB,
            bounds_error=False,
            fill_value=0.0,
        )
        if bandpass_center_THz is None or bandpass_span_THz is None:
            start, stop = self.start_THz, self.stop_THz
        else:
            start = bandpass_center_THz - bandpass_span_THz / 2
            stop = bandpass_center_THz + bandpass_span_THz / 2
        self.atten = lambda f: (
            max(float(atten_interp(f)), 0.0) if start < f < stop else MAX_ATTEN_DB
        )
        self.atten_description = (
            f"flattening profile ({peak_wl.size} peaks, max {max_atten_dB} dB, "
            f"window [{start:.3f}, {stop:.3f}] THz)"
        )
        self.log.info("%s: %s", self.name, self.atten_description)
        return peak_wl, peak_pw

    def write_profile(self, amp: np.ndarray | None = None, phase: np.ndarray | None = None) -> None:
        """Upload the current (or given) attenuation + phase to the device.

        ``amp``/``phase`` arrays must be sampled on ``self.freq_THz``.
        """
        if amp is not None:
            if len(amp) != len(self.freq_THz):
                raise ValueError(f"amp has {len(amp)} points, grid has {len(self.freq_THz)}")
            values = np.asarray(amp, dtype=float)
            self.atten = lambda f: float(values[int(round((f - self.start_THz) * 1000))])
        if phase is not None:
            if len(phase) != len(self.freq_THz):
                raise ValueError(f"phase has {len(phase)} points, grid has {len(self.freq_THz)}")
            pvalues = np.asarray(phase, dtype=float)
            self.phase = lambda f: float(pvalues[int(round((f - self.start_THz) * 1000))])
        lines = [f"{f:.3f}\t{self.atten(f):.1f}\t{self.phase(f):.6f}\t1" for f in self.freq_THz]

        def op() -> None:
            self.transport.load_profile("\n".join(lines) + "\n")  # type: ignore[attr-defined]

        self._io(op)
        self.log.info("%s: profile uploaded (%d points)", self.name, len(lines))

    def profile_arrays(self) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """(freq_THz, atten_dB, phase_rad) sampled on the device grid —
        what the GUIs plot."""
        atten = np.array([self.atten(f) for f in self.freq_THz])
        phase = np.array([self.phase(f) for f in self.freq_THz])
        return self.freq_THz, atten, phase

    def status(self) -> dict:
        return {
            "start_THz": self.start_THz,
            "stop_THz": self.stop_THz,
            "atten": self.atten_description or "flat (0 dB)",
            "phase": self.phase_description or "flat (0 rad)",
        }
