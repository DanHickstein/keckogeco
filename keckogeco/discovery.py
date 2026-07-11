"""Instrument auto-discovery for the KeckLFC rack (``keckogeco-find``).

Adopted from Dan Hickstein's ``find_instruments.py`` (developed and tested
on LAPTOP-LFC2), with the output retargeted at ``keckogeco.toml``: the
discovery result *is* the ``[devices.*]`` configuration the rest of the
package loads.

How it works:

1. Loads the existing config (if any) and *verifies* each previously-found
   serial device on its recorded USB adapter (fast path, resolved by USB
   adapter serial number, so it survives COM-port renumbering).
2. Runs discovery probes only on ports not claimed by a verified device.
3. Scans the VISA side (GPIB adapter, USB-TMC instruments) and reports
   clearly if the GPIB driver stack looks broken.
4. Prints a FOUND / NOT FOUND / NOT CHECKED report and rewrites the
   ``[devices.*]`` blocks (discovery bookkeeping keys like ``usb_serial``
   are stored in the block and ignored by the drivers).

All serial probing is read-only. Devices with unsafe binary protocols
(ORION laser, LFC-3751) are never probed.

Other code can use the fast path directly::

    from keckogeco.discovery import quick_verify
    ports = quick_verify()      # {device_key: "COM17", ...}, verified only

Usage::

    keckogeco-find                # verify config, then discover the rest
    keckogeco-find --rescan       # ignore config, full discovery
    keckogeco-find --no-gpib      # skip the VISA/GPIB scan
    keckogeco-find --ports COM3 COM17
    keckogeco-find -v             # show all probe responses
"""

from __future__ import annotations

import argparse
import contextlib
import logging
import re
import time
from datetime import datetime
from pathlib import Path

from .config import CONFIG_FILENAME, ConfigError, find_config_file, load_config

log = logging.getLogger(__name__)

# --------------------------------------------------------------------------
# Low-level probe helpers
# --------------------------------------------------------------------------


def read_response(ser, settle=0.4, quiet=0.15, max_wait=1.0):
    """Read whatever the device sends back. Waits ``settle`` s first, then
    keeps reading until no new bytes arrive for ``quiet`` s or ``max_wait``
    s total. Slow multi-line devices (OZ Optics dumps) override these via
    the probe's ``read_kwargs``."""
    time.sleep(settle)
    data = bytearray()
    t_start = time.time()
    t_last = time.time()
    while (time.time() - t_start) < max_wait:
        n = ser.in_waiting
        if n:
            data.extend(ser.read(n))
            t_last = time.time()
        elif data and (time.time() - t_last) > quiet:
            break
        else:
            time.sleep(0.02)
    return bytes(data)


def printable(raw, limit=200):
    """Best-effort printable version of a raw response for the report."""
    text = raw.decode("utf-8", errors="replace")
    text = text.replace("\r", "\\r").replace("\n", "\\n")
    return text[:limit] + ("..." if len(text) > limit else "")


def tc720_probe_message():
    """Read-only 'read input1 temperature' message for the TC-720."""
    from .drivers.tec_tc720 import _build_message

    return _build_message("01").encode("ascii")


def agiltron_frame(cmd: int) -> bytes:
    return bytes([0x01, cmd, 0x00, 0x00])


# --------------------------------------------------------------------------
# IDN response classification (for the generic *IDN? probe)
# --------------------------------------------------------------------------

IDN_SIGNATURES = [
    # (substring in IDN response, human name, driver, extra config options)
    ("GPD-", "GW Instek GPD-4303S DC supply", "instek_psu", {"model": "GPD-4303S"}),
    ("GPP-1326", "GW Instek GPP-1326 DC supply", "instek_psu", {"model": "GPP-1326"}),
    ("GPP-", "GW Instek GPP DC supply", "instek_psu", {}),
    ("SIM900", "SRS SIM900 mainframe", "srs_sim900", {}),
    ("Stanford_Research", "SRS instrument", "srs_sim900", {}),
    ("CNT-9", "Pendulum CNT-90 counter", "pendulum_cnt90", {}),
    ("Clarity", "Wavelength References Clarity", "clarity", {}),
    ("Wavelength", "Wavelength References Clarity", "clarity", {}),
]


def classify_idn(raw: bytes):
    """Map an ``*IDN?`` response to (name, driver, options)."""
    text = raw.decode("utf-8", errors="replace")
    for needle, device, driver, options in IDN_SIGNATURES:
        if needle.lower() in text.lower():
            return device, driver, options
    return f"Unrecognized SCPI instrument (IDN: {text.strip()})", "?", {}


# --------------------------------------------------------------------------
# Probe definitions
#
# Each probe: dict with
#   name        : label used in report and as the verification key in the
#                 saved config -- renaming a probe invalidates old entries
#   device      : identified device name (None = resolve via IDN_SIGNATURES)
#   driver      : keckogeco.drivers module the entry should use
#   visa        : True if the driver expects an ASRLn::INSTR address,
#                 False if it takes the raw COM port
#   baud        : baud rate to use
#   tx          : bytes to send
#   match       : function(raw_bytes) -> bool
#   verify_match: optional looser matcher used on the fast path
#   confidence  : 'high' or 'low'
#   pre_delay   : seconds to wait after opening the port before sending
#                 (the Arduino resets on open and needs ~2 s to boot)
#   read_kwargs : optional overrides for read_response timing
#   retries     : re-send count for devices that drop the first command
# --------------------------------------------------------------------------


def _idn_probe(baud):
    return {
        "name": f"*IDN? @ {baud}",
        "device": None,  # resolved from IDN_SIGNATURES
        "driver": None,
        "visa": True,
        "baud": baud,
        "tx": b"*IDN?\r\n",
        "match": lambda raw: len(raw.strip()) > 3 and b"," in raw,
        "confidence": "high",
        "pre_delay": 0.0,
        # The GW Instek GPP drops the first command(s) after sitting idle,
        # so re-send *IDN? if the first try gets silence. Only safe for
        # idempotent SCPI queries (NOT the Eaton PDU: its console counts
        # every string as a failed login).
        "retries": 2,
    }


PROBES = [
    # Generic SCPI identification at the rack's known baud rates:
    # 9600: InstekGPD/SRS/Clarity/RbClock; 115200: Instek GPP; 19200: n/a
    _idn_probe(9600),
    _idn_probe(115200),
    _idn_probe(19200),
    # Amonics EDFA: SCPI-like but no *IDN?; model query instead.
    {
        "name": ":CAL:SYS:MODEL? @ 19200",
        "device": "Amonics EDFA",
        "driver": "amonics_edfa",
        "visa": True,
        "baud": 19200,
        "tx": b":CAL:SYS:MODEL?\r\n",
        "match": lambda raw: b"EDFA" in raw.upper(),
        "confidence": "high",
        "pre_delay": 0.0,
    },
    # OZ Optics VOA: 'CD' dumps the config block (read-only), multi-line,
    # ends with 'Done'; unknown commands get 'Error-2'. The dump arrives
    # line by line with pauses, so allow a much longer read window.
    {
        "name": "CD @ 9600",
        "device": "OZ Optics VOA",
        "driver": "oz_voa",
        "visa": True,
        "baud": 9600,
        "tx": b"CD\r\n",
        "match": lambda raw: (
            b"Done" in raw or b"ATTEN" in raw.upper() or b"WAVELENGTH" in raw.upper()
        ),
        "confidence": "high",
        "pre_delay": 0.0,
        "read_kwargs": {"settle": 0.5, "quiet": 1.0, "max_wait": 8.0},
    },
    # Thorlabs SC10-style shutter (hk_shutter): responses end with a '>'
    # prompt. Do NOT match the command echo alone: login consoles (Eaton)
    # also echo, but then ask for a password.
    {
        "name": "ens? @ 9600",
        "device": "Shutter controller (Thorlabs SC10 style)",
        "driver": "hk_shutter",
        "visa": False,
        "baud": 9600,
        "tx": b"ens?\r",
        "match": lambda raw: raw.strip().endswith(b">"),
        "confidence": "high",
        "pre_delay": 0.0,
    },
    # SRS FS725 Rb clock: 'SN?' returns the bare serial number.
    {
        "name": "SN? @ 9600",
        "device": "SRS FS725 Rb frequency standard",
        "driver": "rb_clock",
        "visa": True,
        "baud": 9600,
        "tx": b"SN?\r",
        "match": lambda raw: raw.strip().isdigit() and len(raw.strip()) >= 3,
        "confidence": "high",
        "pre_delay": 0.0,
    },
    # OZ Optics units that do not support 'CD' answer 'SN?' with
    # 'Serial No.: xxxxxx-xx, ...' + 'Done' (the OZ protocol signature).
    {
        "name": "SN? @ 9600 (OZ Optics)",
        "device": "OZ Optics VOA",
        "driver": "oz_voa",
        "visa": True,
        "baud": 9600,
        "tx": b"SN?\r",
        "match": lambda raw: b"serial no" in raw.lower() and b"done" in raw.lower(),
        "confidence": "high",
        "pre_delay": 0.0,
        "read_kwargs": {"settle": 0.5, "quiet": 1.0, "max_wait": 8.0},
    },
    # TE Tech TC-720: read-only 'read input1'; reply is 8 bytes '*xxxxcc^'.
    {
        "name": "TC-720 read input1 @ 230400",
        "device": "TE Tech TC-720 TEC controller",
        "driver": "tec_tc720",
        "visa": False,
        "baud": 230400,
        "tx": tc720_probe_message(),
        "match": lambda raw: raw.startswith(b"*") and raw.rstrip().endswith(b"^"),
        "confidence": "high",
        "pre_delay": 0.0,
    },
    # Agiltron 2x2 switch: binary 4-byte protocol; 0x13 = status query
    # (read-only, does not move the switch); reply x01 x13 <port> x00.
    {
        "name": "Agiltron status 0x13 @ 9600",
        "device": "Agiltron 2x2 switch",
        "driver": "agiltron_switch",
        "visa": False,
        "baud": 9600,
        "tx": agiltron_frame(0x13),
        "match": lambda raw: raw.startswith(b"\x01\x13") and len(raw) >= 4,
        "confidence": "high",
        "pre_delay": 0.0,
    },
    # Agiltron 1x6 SelfAlign: 0x06 = mode/version query (read-only). The
    # 2x2 also answers 0x06 but is claimed by the status probe first, so an
    # 0x06-only responder is the 1x6 (not ported; reported only).
    {
        "name": "Agiltron version 0x06 @ 9600",
        "device": "Agiltron 1x6 SelfAlign switch (driver not ported)",
        "driver": "?",
        "visa": False,
        "baud": 9600,
        "tx": agiltron_frame(0x06),
        "match": lambda raw: raw.startswith(b"\x01\x06") and len(raw) >= 4,
        "confidence": "high",
        "pre_delay": 0.0,
    },
    # Eaton ePDU serial console: pressing Enter yields a login prompt.
    # NOTE: the console counts every probe string as a failed login and
    # locks out after 3 failures, so verification (already anchored to the
    # USB adapter serial) accepts lockout replies too.
    {
        "name": "Enter (login prompt) @ 9600",
        "device": "Eaton PDU (serial console)",
        "driver": "eaton_pdu",
        "visa": True,
        "baud": 9600,
        "tx": b"\r\n",
        "match": lambda raw: any(
            k in raw.lower() for k in (b"eaton", b"epdu", b"login", b"user", b"password")
        ),
        "verify_match": lambda raw: any(
            k in raw.lower()
            for k in (b"eaton", b"epdu", b"login", b"user", b"password", b"lock", b"attempt")
        ),
        "confidence": "high",
        "pre_delay": 0.0,
    },
    # Arduino relay: 'help' prints usage. The Arduino auto-resets when the
    # port opens, so wait ~2 s before talking to it.
    {
        "name": "help @ 9600 (after 2 s Arduino boot)",
        "device": "Arduino relay circuit",
        "driver": "arduino_relay",
        "visa": False,
        "baud": 9600,
        "tx": b"help\r\n",
        "match": lambda raw: any(
            k in raw.lower() for k in (b"relay", b"threshold", b"yj", b"arduino")
        ),
        "confidence": "high",
        "pre_delay": 2.0,
    },
    # Pritel amplifier, primary probe: 'FA INPUT?' is read-only.
    {
        "name": "FA INPUT? @ 9600",
        "device": "Pritel optical amplifier",
        "driver": "pritel_amp",
        "visa": True,
        "baud": 9600,
        "tx": b"FA INPUT?\r",
        "match": lambda raw: b"input power" in raw.lower() or b"pritel" in raw.lower(),
        "confidence": "high",
        "pre_delay": 0.0,
        "read_kwargs": {"settle": 0.5, "quiet": 0.5, "max_wait": 4.0},
    },
    # Pritel backup probe: 'READY?' -> 'PriTel FA Ready'.
    {
        "name": "READY? @ 9600",
        "device": "Pritel optical amplifier",
        "driver": "pritel_amp",
        "visa": True,
        "baud": 9600,
        "tx": b"READY?\r",
        "match": lambda raw: b"pritel" in raw.lower() or b"fa ready" in raw.lower(),
        "confidence": "high",
        "pre_delay": 0.0,
    },
    # Eaton console that ignored the bare Enter above: any input makes it
    # print its login banner, which names the product explicitly.
    {
        "name": "READY? @ 9600 (Eaton banner)",
        "device": "Eaton PDU (serial console)",
        "driver": "eaton_pdu",
        "visa": True,
        "baud": 9600,
        "tx": b"READY?\r\n",
        "match": lambda raw: b"eaton" in raw.lower() or b"epdu" in raw.lower(),
        "verify_match": lambda raw: any(
            k in raw.lower()
            for k in (b"eaton", b"epdu", b"login", b"user", b"password", b"lock", b"attempt")
        ),
        "confidence": "high",
        "pre_delay": 0.0,
    },
    # Login-protected serial console with an anonymous banner.
    {
        "name": "READY? @ 9600 (password prompt)",
        "device": "Password-protected serial console (likely Eaton PDU - verify manually!)",
        "driver": "eaton_pdu",
        "visa": True,
        "baud": 9600,
        "tx": b"READY?\r\n",
        "match": lambda raw: b"password" in raw.lower() or b"login" in raw.lower(),
        "confidence": "low",
        "pre_delay": 0.0,
    },
    # Anything else that talks at 9600: report it, make no claim.
    {
        "name": "READY? @ 9600 (unknown responder)",
        "device": "Unknown device (responds at 9600 baud - verify manually!)",
        "driver": "?",
        "visa": False,
        "baud": 9600,
        "tx": b"READY?\r\n",
        "match": lambda raw: len(raw.strip()) > 0,
        "confidence": "low",
        "pre_delay": 0.0,
    },
]

PROBES_BY_NAME = {p["name"]: p for p in PROBES}

# Known unit counts: once this many devices of a driver have been found,
# its probes are skipped on the remaining ports.
MAX_INSTANCES = {
    "arduino_relay": 1,
    "oz_voa": 3,  # 1310 / 1550 / 2000 nm units
    "amonics_edfa": 3,
    "eaton_pdu": 2,
    "clarity": 1,
    "hk_shutter": 1,
    "waveshaper": 2,
    "agiltron_switch": 1,
}

# --------------------------------------------------------------------------
# What we expect to exist (drives the FOUND / NOT FOUND report)
# --------------------------------------------------------------------------

EXPECTED_SERIAL = [
    ("amonics_edfa", "Amonics EDFA(s)"),
    ("pritel_amp", "Pritel optical amplifier"),
    ("instek_psu", "GW Instek DC supplies (GPD-4303S + GPP)"),
    ("clarity", "Wavelength References Clarity laser"),
    ("rb_clock", "SRS FS725 Rb frequency standard"),
    ("oz_voa", "OZ Optics VOA (x3: 1310/1550/2000 nm)"),
    ("tec_tc720", "TE Tech TC-720 TEC controller(s)"),
    ("eaton_pdu", "Eaton PDU serial console"),
    ("hk_shutter", "Shutter controller (SC10 style)"),
    ("arduino_relay", "Arduino relay circuit"),
    ("waveshaper", "Finisar WaveShaper (passive USB match)"),
    ("agiltron_switch", "Agiltron 2x2 switch"),
]

EXPECTED_GPIB = [
    ("srs_sim900", "SRS SIM900 mainframe"),
    ("pendulum_cnt90", "Pendulum CNT-90 counter"),
    ("agilent_86142b", "Agilent 86142B OSA"),
    ("keysight_fg33500", "Keysight 33500-series function generator (USB-TMC)"),
    ("tds2024c", "Tektronix TDS2024C oscilloscope (USB-TMC)"),
]

NOT_CHECKED = [
    ("orion_laser", "binary protocol, no safe ID-only query"),
    ("tec_lfc3751", "checksum packet protocol, no safe generic probe"),
    ("red_pitaya", "Ethernet device, out of scope for this scan"),
    ("usb2408", "MCC DAQ, managed through InstaCal, not a COM port"),
]

GPIB_SIGNATURES = [
    ("SIM900", "SRS SIM900 mainframe", "srs_sim900"),
    ("Stanford_Research", "SRS instrument", "srs_sim900"),
    ("CNT-9", "Pendulum CNT-90 counter", "pendulum_cnt90"),
    ("33500", "Keysight 33500-series function generator", "keysight_fg33500"),
    ("33512", "Keysight 33512B function generator", "keysight_fg33500"),
    ("86142", "Agilent 86142B OSA", "agilent_86142b"),
    ("TDS 2024C", "Tektronix TDS2024C oscilloscope", "tds2024c"),
]

# Identified purely from USB descriptor metadata -- nothing is ever sent.
PASSIVE_SIGNATURES = [
    {
        "vid_pid": "0403:6011",  # FT4232 quad UART
        "serial_prefix": "WS",  # e.g. WS201904D = WaveShaper SN201904
        "device": "Finisar WaveShaper (matched by USB serial, never probed)",
        "driver": "waveshaper",
    },
]

# USB-TMC instruments that do not answer *IDN?, classified by VID:PID.
USBTMC_MODELS = {
    (0x0699, 0x03A6): ("Tektronix TDS2024C oscilloscope", "tds2024c"),
}
USB_VENDORS = {
    0x0699: "Tektronix",
    0x0957: "Keysight/Agilent",
    0x0B21: "Yokogawa",
    0x1313: "Thorlabs",
}


# --------------------------------------------------------------------------
# Serial discovery / verification
# --------------------------------------------------------------------------


def gather_ports(only=None):
    from serial.tools import list_ports

    ports = sorted(list_ports.comports(), key=lambda p: p.device)
    if only:
        wanted = {p.upper() for p in only}
        ports = [p for p in ports if p.device.upper() in wanted]
    return ports


def visa_addr_for(com_port: str) -> str:
    return f"ASRL{com_port.upper().replace('COM', '')}::INSTR"


def address_for(probe_or_entry: dict, com_port: str, usb_serial: str | None = None) -> str:
    """The address string the matching driver expects."""
    if probe_or_entry.get("driver") == "waveshaper" and usb_serial:
        # WS201904D -> SN201904 (drop 'WS' prefix and trailing port letter)
        return "SN" + re.sub(r"[A-D]$", "", usb_serial[2:])
    if probe_or_entry.get("visa", False):
        return visa_addr_for(com_port)
    return com_port


def extract_token(probe_name: str, response_text: str) -> str | None:
    """A short distinctive string from the ID response, used later to
    confirm the *same unit* is still on this adapter (e.g. distinguishes
    the three Amonics EDFAs by model)."""
    text = response_text.replace("\\r", " ").replace("\\n", " ").strip()
    if probe_name.startswith("*IDN?"):
        parts = [p.strip() for p in text.split(",")]
        if len(parts) >= 2:
            return parts[1]  # model field of the IDN string
        return text[:30] or None
    if probe_name.startswith(":CAL:SYS:MODEL?"):
        return text.split()[0] if text.split() else None
    if probe_name.startswith("SN? @ 9600 (OZ"):
        m = re.search(r"Serial No\.?:?\s*([\w-]+)", text, re.IGNORECASE)
        return f"NO-{m.group(1)}" if m else None
    if probe_name.startswith("CD"):
        m = re.search(r"WAVELENGTH:\s*(\d+)", text)
        if m:
            return f"WL{m.group(1)}"
        m = re.search(r"NO:\s*([\w.-]+)", text)
        return f"NO-{m.group(1)}" if m else None
    return None


def run_probe(port: str, probe: dict):
    """Open ``port``, send the probe, return the raw response (b'' on no
    answer) or None if the port could not be opened."""
    import serial

    try:
        ser = serial.Serial(
            port=port,
            baudrate=probe["baud"],
            timeout=0.5,
            write_timeout=1,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
            bytesize=serial.EIGHTBITS,
        )
    except serial.SerialException as exc:
        log.warning("could not open %s: %s", port, exc)
        return None
    try:
        if probe["pre_delay"]:
            time.sleep(probe["pre_delay"])
        raw = b""
        for _ in range(1 + probe.get("retries", 0)):
            ser.reset_input_buffer()
            ser.reset_output_buffer()
            ser.write(probe["tx"])
            ser.flush()
            raw = read_response(ser, **probe.get("read_kwargs", {}))
            if raw:
                return raw
        return raw
    except (OSError, serial.SerialException) as exc:
        log.warning("I/O error on %s: %s", port, exc)
        return b""
    finally:
        with contextlib.suppress(Exception):
            ser.close()


def completed_drivers(instruments: dict) -> set:
    """Drivers whose expected unit count (MAX_INSTANCES) is already met."""
    counts: dict[str, int] = {}
    for entry in instruments.values():
        counts[entry.get("driver")] = counts.get(entry.get("driver"), 0) + 1
    return {d for d, n in MAX_INSTANCES.items() if counts.get(d, 0) >= n}


def discover_port(port: str, verbose=False, skip_drivers=frozenset()):
    """Run all probes against one port. Returns a hit dict, 'busy', or None."""
    low_confidence_hit = None
    for probe in PROBES:
        if probe["driver"] in skip_drivers:
            continue
        raw = run_probe(port, probe)
        if raw is None:
            return "busy"
        if verbose and raw:
            print(f"    {probe['name']}: {printable(raw)}")  # noqa: T201
        if raw and probe["match"](raw):
            if probe["device"] is None:
                device, driver, options = classify_idn(raw)
            else:
                device, driver, options = probe["device"], probe["driver"], {}
            response = printable(raw, 300)
            hit = {
                "device": device,
                "driver": driver,
                "visa": probe.get("visa", False),
                "options": options,
                "probe": probe["name"],
                "response": response,
                "match_token": extract_token(probe["name"], response),
                "confidence": probe["confidence"],
            }
            if probe["confidence"] == "high":
                return hit
            if low_confidence_hit is None:
                low_confidence_hit = hit
        time.sleep(0.1)
    return low_confidence_hit


def passive_match(port_info):
    """Identify a port from USB descriptor metadata alone (nothing sent)."""
    if port_info.vid is None:
        return None
    vid_pid = f"{port_info.vid:04X}:{port_info.pid:04X}"
    for sig in PASSIVE_SIGNATURES:
        if vid_pid == sig["vid_pid"] and (port_info.serial_number or "").upper().startswith(
            sig["serial_prefix"]
        ):
            return sig
    return None


def verify_entry(entry: dict, ports_by_serial: dict, ports_by_name: dict) -> str | None:
    """Confirm a configured device is still reachable. Resolves the port by
    USB adapter serial number first (robust against COM renumbering), then
    by the stored port name. Returns the resolved COM port, or None."""
    port = None
    usb_serial = entry.get("usb_serial")
    if usb_serial and usb_serial in ports_by_serial:
        port = ports_by_serial[usb_serial].device
    elif entry.get("port") in ports_by_name:
        port = entry["port"]
    if port is None:
        return None

    if entry.get("passive"):
        return port  # metadata match is the whole identification

    probe = PROBES_BY_NAME.get(entry.get("probe"))
    if probe is None:
        return None
    raw = run_probe(port, probe)
    matcher = probe.get("verify_match", probe["match"])
    if not raw or not matcher(raw):
        return None
    token = entry.get("match_token")
    if token and token != extract_token(probe["name"], printable(raw, 300)):
        return None  # right protocol, wrong unit
    return port


def make_key(driver: str, token: str | None, usb_serial: str | None, existing: dict) -> str:
    base = driver if driver and driver != "?" else "unknown_serial_device"
    if token:
        base = f"{base}_{re.sub(r'[^A-Za-z0-9._-]', '-', token)}"
    elif usb_serial:
        base = f"{base}_{usb_serial}"
    key, n = base, 2
    while key in existing:
        key = f"{base}_{n}"
        n += 1
    return key


# --------------------------------------------------------------------------
# GPIB / VISA scan
# --------------------------------------------------------------------------


def classify_gpib_idn(idn: str):
    for needle, device, driver in GPIB_SIGNATURES:
        if needle.lower() in idn.lower():
            return device, driver
    return f"Unrecognized VISA instrument (IDN: {idn})", "?"


def classify_usbtmc_addr(addr: str):
    """Classify a USB-TMC resource that gave no ``*IDN?`` reply, by VID:PID."""
    m = re.match(r"USB\d*::0x([0-9A-Fa-f]{4})::0x([0-9A-Fa-f]{4})::", addr)
    if not m:
        return None, "?"
    vid, pid = int(m.group(1), 16), int(m.group(2), 16)
    if (vid, pid) in USBTMC_MODELS:
        return USBTMC_MODELS[(vid, pid)]
    if vid in USB_VENDORS:
        return f"{USB_VENDORS[vid]} instrument (VID:PID {m.group(1)}:{m.group(2)})", "?"
    return None, "?"


def scan_gpib(verbose=False):
    """Scan GPIB and USB-TMC instruments through pyvisa.
    Returns (found_entries, notes). Diagnoses missing-driver situations."""
    found, notes = [], []
    try:
        import pyvisa
    except ImportError:
        notes.append("pyvisa is not installed -> GPIB scan skipped. (pip install pyvisa)")
        return found, notes
    try:
        rm = pyvisa.ResourceManager()
        notes.append(f"VISA backend: {rm.visalib}")
    except Exception as exc:  # noqa: BLE001 - report any backend failure
        notes.append(f"No VISA backend could be loaded ({exc}).")
        notes.append("Install NI-VISA (with NI-488.2) or Keysight IO Libraries Suite.")
        return found, notes

    try:
        resources = list(rm.list_resources("?*"))
    except Exception as exc:  # noqa: BLE001
        notes.append(f"list_resources() failed: {exc}")
        return found, notes

    gpib_instr = [
        r for r in resources if r.upper().startswith("GPIB") and r.upper().endswith("INSTR")
    ]
    gpib_board = [r for r in resources if "INTFC" in r.upper()]
    usbtmc = [r for r in resources if r.upper().startswith("USB") and r.upper().endswith("INSTR")]

    if not gpib_instr and not gpib_board:
        notes.append("No GPIB interface or instruments visible to VISA.")
        notes.append("If the GPIB-USB adapter is plugged in, this confirms a driver problem:")
        notes.append("  - NI GPIB-USB-HS: needs the NI-488.2 driver; check it appears in NI MAX.")
        notes.append("  - Agilent/Keysight 82357A/B: needs Keysight IO Libraries.")
        notes.append("  - Check Windows Device Manager for an unrecognized USB device.")
    elif gpib_board and not gpib_instr:
        notes.append(f"GPIB interface {gpib_board} is present but no instruments answered.")
        notes.append("Check GPIB cabling and that the instruments are powered on.")

    for addr in gpib_instr + usbtmc:
        try:
            inst = rm.open_resource(addr)
            inst.timeout = 2000
            idn = inst.query("*IDN?").strip()
            inst.close()
        except Exception as exc:  # noqa: BLE001 - any failure = no IDN
            if verbose:
                print(f"    {addr}: no *IDN? response ({exc})")  # noqa: T201
            device, driver = classify_usbtmc_addr(addr)
            found.append(
                {
                    "device": (
                        f"{device} (no *IDN? reply, identified by VID:PID)"
                        if device
                        else "Instrument present but no *IDN? response"
                    ),
                    "driver": driver,
                    "address": addr,
                }
            )
            continue
        if verbose:
            print(f"    {addr}: {idn}")  # noqa: T201
        device, driver = classify_gpib_idn(idn)
        found.append(
            {
                "device": device,
                "driver": driver,
                "address": addr,
                "response": idn,
                "match_token": idn.split(",")[1].strip() if idn.count(",") >= 2 else None,
            }
        )
    return found, notes


# --------------------------------------------------------------------------
# Config I/O (TOML [devices.*] blocks; discovery bookkeeping kept inline)
# --------------------------------------------------------------------------


def load_existing(config_path: Path) -> dict:
    """{device_key: entry-dict} from the current config, [devices.*] only."""
    try:
        cfg = load_config(config_path)
    except ConfigError:
        return {}
    entries = {}
    for key, dev in cfg.devices.items():
        entry = {"driver": dev.driver, "address": dev.address, "device": dev.name}
        entry.update(dev.options)
        if not str(dev.address).upper().startswith(("GPIB", "USB", "SN")):
            entry.setdefault(
                "port",
                dev.address
                if dev.address.upper().startswith("COM")
                else f"COM{dev.address.upper().removeprefix('ASRL').split(':')[0]}"
                if dev.address.upper().startswith("ASRL")
                else dev.address,
            )
        entries[key] = entry
    return entries


def save_config(instruments: dict, config_path: Path) -> None:
    """Rewrite the [devices.*] blocks of the config, preserving everything
    else (server/logging/alerts sections, comments)."""
    import tomlkit

    if config_path.exists():
        doc = tomlkit.parse(config_path.read_text(encoding="utf-8"))
    else:
        doc = tomlkit.document()
        doc.add(tomlkit.comment("Generated by keckogeco-find. See config/README.md."))

    devices = tomlkit.table()
    for key in sorted(instruments):
        entry = instruments[key]
        block = tomlkit.table()
        for field in ("driver", "address", "name"):
            value = entry.get(field) or entry.get("device" if field == "name" else field)
            if value is not None:
                block[field] = value
        if entry.get("driver") in (None, "?"):
            block["enabled"] = False
            block.add(tomlkit.comment("unidentified device - review manually"))
        for extra_key in (
            "model",
            "usb_serial",
            "vid_pid",
            "adapter",
            "probe",
            "match_token",
            "confidence",
            "passive",
            "found_on",
            "verified_on",
        ):
            if entry.get(extra_key) not in (None, ""):
                block[extra_key] = entry[extra_key]
        devices[key] = block
    doc["devices"] = devices
    config_path.write_text(tomlkit.dumps(doc), encoding="utf-8")
    print(f"\nConfiguration saved to {config_path}")  # noqa: T201


def quick_verify(config_path: str | Path | None = None) -> dict[str, str]:
    """Fast startup path for other code: verify only the configured serial
    devices on their recorded adapters (no broad scanning).

    Returns ``{device_key: "COMx"}`` for every device that verified.
    """
    try:
        path = find_config_file(config_path)
    except ConfigError:
        return {}
    existing = load_existing(path)
    ports = gather_ports()
    ports_by_serial = {p.serial_number: p for p in ports if p.serial_number}
    ports_by_name = {p.device: p for p in ports}
    resolved = {}
    for key, entry in existing.items():
        if "port" not in entry:
            continue  # GPIB / USB-TMC / wsapi devices
        port = verify_entry(entry, ports_by_serial, ports_by_name)
        if port:
            resolved[key] = port
    return resolved


# --------------------------------------------------------------------------
# Main scan + report
# --------------------------------------------------------------------------


def report(instruments: dict, gpib_notes: list, unidentified_ports: list) -> None:
    p = print  # noqa: T201 - this is the CLI's output
    p("")
    p("=" * 78)
    p("INSTRUMENT INVENTORY")
    p("=" * 78)

    by_driver: dict[str, list] = {}
    for key, entry in instruments.items():
        by_driver.setdefault(entry.get("driver"), []).append((key, entry))

    def print_status(driver: str, friendly: str) -> None:
        hits = by_driver.get(driver, [])
        if hits:
            for _key, entry in hits:
                where = entry.get("address", "?")
                if entry.get("usb_serial"):
                    where += f" (usb serial {entry['usb_serial']})"
                flag = "" if entry.get("verified_on") else " [newly discovered]"
                p(f"  FOUND      {friendly:45s} -> {where}{flag}")
                extra = entry.get("match_token") or entry.get("response", "")
                if extra:
                    p(f"             id: {extra}")
        else:
            p(f"  NOT FOUND  {friendly}")

    p("\n--- Serial (COM port) instruments ---")
    for driver, friendly in EXPECTED_SERIAL:
        print_status(driver, friendly)

    p("\n--- GPIB / USB-TMC instruments ---")
    for driver, friendly in EXPECTED_GPIB:
        print_status(driver, friendly)
    for note in gpib_notes:
        p(f"  [gpib] {note}")

    unknown = [(k, e) for k, e in instruments.items() if e.get("driver") in (None, "?")]
    if unknown:
        p("\n--- Responded, but unrecognized ---")
        for _key, entry in unknown:
            p(f"  {entry.get('address')}: {entry.get('device')} ({entry.get('response', '')})")

    p("\n--- Not checked (unsafe or out of scope to probe) ---")
    for driver, reason in NOT_CHECKED:
        p(f"  NOT CHECKED  {driver:30s} ({reason})")

    if unidentified_ports:
        p("\n--- Ports with no response (device off, unplugged adapter, or an")
        p("    unprobed device from the list above) ---")
        for port_info in unidentified_ports:
            sn = f", usb serial {port_info.serial_number}" if port_info.serial_number else ""
            p(f"  {port_info.device}: {port_info.description}{sn}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Find and verify all rack instruments.")
    parser.add_argument(
        "--rescan", action="store_true", help="ignore saved config, run full discovery"
    )
    parser.add_argument("--no-gpib", action="store_true", help="skip the VISA/GPIB scan")
    parser.add_argument(
        "--ports", nargs="+", metavar="COMx", help="restrict the serial scan to these ports"
    )
    parser.add_argument("--config", default=None, help="config file path")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    try:
        config_path = find_config_file(args.config)
    except ConfigError:
        config_path = Path("config") / CONFIG_FILENAME
        config_path.parent.mkdir(parents=True, exist_ok=True)
        print(f"No existing config; will create {config_path}")  # noqa: T201

    ports = gather_ports(args.ports)
    ports_by_serial = {p.serial_number: p for p in ports if p.serial_number}
    ports_by_name = {p.device: p for p in ports}

    instruments: dict[str, dict] = {}
    assigned_ports: set[str] = set()
    now = datetime.now().isoformat(timespec="seconds")

    # ---- fast path: verify previously found devices ----
    existing = {} if args.rescan else load_existing(config_path)
    if existing:
        print("Verifying devices from saved configuration ...")  # noqa: T201
        for key, entry in existing.items():
            if "port" not in entry:
                instruments[key] = entry  # GPIB rescanned below; wsapi kept
                continue
            if entry.get("driver") in (None, "?") or entry.get("confidence") == "low":
                print(f"  SKIP {key} (unidentified/low confidence) -> will rediscover")  # noqa: T201
                continue
            port = verify_entry(entry, ports_by_serial, ports_by_name)
            if port:
                entry["port"] = port
                entry["address"] = address_for(entry, port, entry.get("usb_serial"))
                entry["verified_on"] = now
                instruments[key] = entry
                assigned_ports.add(port)
                print(f"  OK   {key} -> {port}")  # noqa: T201
            else:
                print(f"  FAIL {key} (was {entry.get('port')}) -> will rediscover")  # noqa: T201

    # ---- discovery on remaining ports ----
    remaining = [p for p in ports if p.device not in assigned_ports]
    unidentified = []
    if remaining:
        print("\nDiscovery scan on unassigned ports ...")  # noqa: T201
    for port_info in remaining:
        sig = passive_match(port_info)
        if sig:
            entry = {
                "device": sig["device"],
                "driver": sig["driver"],
                "port": port_info.device,
                "address": address_for(sig, port_info.device, port_info.serial_number),
                "usb_serial": port_info.serial_number,
                "vid_pid": f"{port_info.vid:04X}:{port_info.pid:04X}",
                "adapter": port_info.description,
                "passive": True,
                "match_token": port_info.serial_number,
                "confidence": "high",
                "found_on": now,
            }
            key = make_key(sig["driver"], port_info.serial_number, None, instruments)
            instruments[key] = entry
            print(  # noqa: T201
                f"  {port_info.device}: {sig['device']} (passive USB match, not probed)"
            )
            continue
        skip = completed_drivers(instruments)
        print(f"  Probing {port_info.device} ({port_info.description}) ...")  # noqa: T201
        hit = discover_port(port_info.device, verbose=args.verbose, skip_drivers=skip)
        if hit == "busy":
            print("    port busy, skipped")  # noqa: T201
            continue
        if hit is None:
            unidentified.append(port_info)
            continue
        entry = {
            "device": hit["device"],
            "driver": hit["driver"],
            "port": port_info.device,
            "address": address_for(hit, port_info.device, port_info.serial_number),
            "usb_serial": port_info.serial_number,
            "vid_pid": (
                f"{port_info.vid:04X}:{port_info.pid:04X}" if port_info.vid is not None else None
            ),
            "adapter": port_info.description,
            "probe": hit["probe"],
            "match_token": hit["match_token"],
            "response": hit["response"],
            "confidence": hit["confidence"],
            "found_on": now,
            **hit.get("options", {}),
        }
        key = make_key(hit["driver"], hit["match_token"], port_info.serial_number, instruments)
        instruments[key] = entry
        print(f"    => {hit['device']}")  # noqa: T201
        print(f"       {hit['probe']} -> {hit['response']}")  # noqa: T201

    # ---- GPIB / VISA ----
    gpib_notes: list[str] = []
    if not args.no_gpib:
        print("\nScanning VISA / GPIB ...")  # noqa: T201
        gpib_found, gpib_notes = scan_gpib(verbose=args.verbose)
        for entry in gpib_found:
            entry["found_on"] = now
            key = make_key(entry["driver"], entry.get("match_token"), None, instruments)
            # keep an existing key for the same address (verified GPIB entry)
            for old_key, old in list(instruments.items()):
                if old.get("address") == entry["address"]:
                    key = old_key
                    break
            instruments[key] = {**instruments.get(key, {}), **entry}
        for note in gpib_notes:
            print(f"  {note}")  # noqa: T201

    save_config(instruments, config_path)
    report(instruments, gpib_notes, unidentified)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
