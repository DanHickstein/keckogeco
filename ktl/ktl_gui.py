#!/usr/bin/env python3
"""Minimal KTL-native operator GUI for the comb service.

Runs on any Keck host with the ``ktl`` Python module — no keckogeco
imports, no third-party packages, just the standard library (tkinter).
Deployed through the astrocomb build tree alongside the dispatcher.

Shows the comb state (decoded from the legacy prime-product
LFC_CHECK_STATUS), per-subsystem lamps, the dispatcher connection and
heartbeat, and a few key live values; provides the three transition
buttons behind confirmation dialogs.

Usage::

    ktl_gui.py [--service comb] [--period 5]
    ktl_gui.py --demo          # fake values, for layout checks anywhere
"""

import argparse
import random
import time
import tkinter as tk
from tkinter import messagebox

# ---------------------------------------------------------------- state

# Subsystem -> prime factor, as encoded by LFC_CHECK_STATUS.
PRIMES = (
    ("Pritel", 2),
    ("EDFA23", 3),
    ("EDFA27", 5),
    ("RF osc", 7),
    ("RF amp", 11),
    ("Rep rate", 13),
)
FULL = 2 * 3 * 5 * 7 * 11 * 13      # 30030
STANDBY = FULL // 2                 # 15015

STATE_COLORS = {
    "FULL COMB": "#2e7d32",
    "STANDBY": "#f9a825",
    "OFF": "#616161",
    "PARTIAL": "#c62828",
    "UNKNOWN": "#9e9e9e",
}


def decode_state(code):
    """Legacy prime-product code -> (state name, {subsystem: bool})."""
    try:
        code = int(code)
    except (TypeError, ValueError):
        return "UNKNOWN", {name: None for name, _ in PRIMES}
    subsystems = {name: code % prime == 0 for name, prime in PRIMES}
    if code == FULL:
        state = "FULL COMB"
    elif code == STANDBY:
        state = "STANDBY"
    elif code == 1:
        state = "OFF"
    else:
        state = "PARTIAL"
    return state, subsystems


def heartbeat_ok(iceclk, max_age_s=30):
    """True if the ICECLK epoch value is recent."""
    try:
        return (time.time() - float(iceclk)) < max_age_s
    except (TypeError, ValueError):
        return False


# ------------------------------------------------------------- backends

VALUE_ROWS = (
    ("LFC_EDFA27_P", "EDFA27 power", "mW"),
    ("LFC_PTAMP_I", "Pritel current", "A"),
    ("LFC_PTAMP_OUT", "Pritel output", "W"),
    ("LFC_RFOSCI_V", "RF osc voltage", "V"),
    ("LFC_RFAMP_V", "RF amp voltage", "V"),
    ("LFC_T_RACK_MID", "Rack temperature", "C"),
    ("LFC_T_GLY_RACK_IN", "Glycol in", "C"),
)

READ_KEYWORDS = ("LFC_CHECK_STATUS", "ICESTA", "ICECLK") + tuple(
    name for name, _, _ in VALUE_ROWS
)


class KtlBackend:
    """Reads and writes through the ktl module."""

    def __init__(self, service):
        import ktl  # provided by kroot; import here so --demo works anywhere

        self.service = ktl.cache(service)

    def read_all(self):
        values = {}
        for name in READ_KEYWORDS:
            try:
                values[name] = self.service[name].read()
            except Exception:
                values[name] = None
        return values

    def write(self, keyword, value):
        self.service[keyword].write(value)


class DemoBackend:
    """Fake values so the layout can be checked without KTL."""

    def __init__(self):
        self.code = STANDBY

    def read_all(self):
        values = {
            "LFC_CHECK_STATUS": self.code,
            "ICESTA": "1",
            "ICECLK": str(int(time.time())),
        }
        for name, _, _ in VALUE_ROWS:
            values[name] = round(random.uniform(0, 30), 2)
        return values

    def write(self, keyword, value):
        if keyword == "LFC_SET_FULL_COMB":
            self.code = FULL
        elif keyword == "LFC_SET_STANDBY":
            self.code = STANDBY
        elif keyword == "LFC_SET_OFF":
            self.code = 1


# ------------------------------------------------------------------ GUI

class CombGui:
    def __init__(self, root, backend, period_s):
        self.root = root
        self.backend = backend
        self.period_ms = max(1, int(period_s * 1000))
        root.title("comb — LFC status (KTL)")

        self.banner = tk.Label(
            root, text="UNKNOWN", font=("TkDefaultFont", 22, "bold"),
            fg="white", bg=STATE_COLORS["UNKNOWN"], width=16, pady=8,
        )
        self.banner.pack(fill="x", padx=10, pady=(10, 6))

        # connection line
        self.conn = tk.Label(root, text="connecting ...", anchor="w")
        self.conn.pack(fill="x", padx=12)

        # subsystem lamps
        lamps = tk.Frame(root)
        lamps.pack(padx=10, pady=6)
        self.lamps = {}
        for column, (name, _) in enumerate(PRIMES):
            canvas = tk.Canvas(lamps, width=26, height=26, highlightthickness=0)
            dot = canvas.create_oval(3, 3, 23, 23, fill="#9e9e9e", outline="black")
            canvas.grid(row=0, column=column, padx=12)
            tk.Label(lamps, text=name).grid(row=1, column=column, padx=12)
            self.lamps[name] = (canvas, dot)

        # key values
        table = tk.Frame(root)
        table.pack(padx=12, pady=6)
        self.values = {}
        for row, (name, label, units) in enumerate(VALUE_ROWS):
            tk.Label(table, text=label, anchor="w", width=18).grid(row=row, column=0, sticky="w")
            var = tk.StringVar(value="—")
            tk.Label(table, textvariable=var, anchor="e", width=10).grid(row=row, column=1)
            tk.Label(table, text=units, anchor="w", width=4, fg="#555").grid(row=row, column=2)
            self.values[name] = var

        # transition buttons
        buttons = tk.Frame(root)
        buttons.pack(pady=(6, 12))
        for text, keyword in (
            ("STANDBY", "LFC_SET_STANDBY"),
            ("FULL COMB", "LFC_SET_FULL_COMB"),
            ("OFF", "LFC_SET_OFF"),
        ):
            tk.Button(
                buttons, text=text, width=12,
                command=lambda t=text, k=keyword: self.transition(t, k),
            ).pack(side="left", padx=6)

        self.refresh()

    def transition(self, label, keyword):
        if not messagebox.askyesno("Confirm", "Send %s?" % label):
            return
        try:
            self.backend.write(keyword, 1)
        except Exception as error:
            messagebox.showerror("Write failed", str(error))

    def refresh(self):
        try:
            values = self.backend.read_all()
        except Exception as error:
            self.conn.config(text="read failed: %s" % error, fg="#c62828")
            values = {}

        state, subsystems = decode_state(values.get("LFC_CHECK_STATUS"))
        self.banner.config(text=state, bg=STATE_COLORS.get(state, "#9e9e9e"))

        for name, on in subsystems.items():
            canvas, dot = self.lamps[name]
            color = "#43a047" if on else "#616161" if on is not None else "#9e9e9e"
            canvas.itemconfig(dot, fill=color)

        for name, var in self.values.items():
            value = values.get(name)
            var.set("—" if value is None else str(value))

        connected = str(values.get("ICESTA")) in ("1", "Connected")
        beating = heartbeat_ok(values.get("ICECLK"))
        if connected and beating:
            text, color = "dispatcher connected, heartbeat OK", "#2e7d32"
        elif connected:
            text, color = "dispatcher connected, heartbeat STALE", "#c62828"
        else:
            text, color = "dispatcher DISCONNECTED (modify icesta=3)", "#c62828"
        self.conn.config(
            text="%s — %s" % (text, time.strftime("%H:%M:%S")), fg=color
        )

        self.root.after(self.period_ms, self.refresh)


def main(argv=None):
    parser = argparse.ArgumentParser(description="KTL-native comb status GUI")
    parser.add_argument("--service", default="comb", help="KTL service name")
    parser.add_argument("--period", type=float, default=5.0, help="poll period, seconds")
    parser.add_argument("--demo", action="store_true", help="fake values, no KTL needed")
    args = parser.parse_args(argv)

    backend = DemoBackend() if args.demo else KtlBackend(args.service)
    root = tk.Tk()
    CombGui(root, backend, args.period)
    root.mainloop()


if __name__ == "__main__":
    main()
