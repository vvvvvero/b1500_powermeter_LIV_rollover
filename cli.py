#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
cli.py
======
Command-line interface for b1500_powermeter_rollover.

Run a headless (no GUI) IV + optical power sweep with optional rollover
detection.  All parameters are passed as command-line arguments.

Usage examples
--------------
List available VISA resources:

    python -m b1500_powermeter_rollover --list

Run a sweep in IV mode:

    python -m b1500_powermeter_rollover \\
        --b1500 GPIB0::17::INSTR \\
        --pm    USB0::0x1313::0x8078::INSTR \\
        --start 0 --stop 2.5 --steps 26 \\
        --compliance 0.5 \\
        --dwell 0.1 \\
        --rollover --method cusum --threshold 90 --window 5 \\
        --output ./results --name MyDevice

© Veronica Gao ZHan  –  May 2026
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import List, Optional


def build_parser() -> argparse.ArgumentParser:
    """Return the fully configured :class:`argparse.ArgumentParser`."""
    p = argparse.ArgumentParser(
        prog="b1500_powermeter_rollover",
        description=(
            "B1500 + Thorlabs power-meter IV sweep with rollover detection.\n"
            "© Veronica Gao ZHan"
        ),
        formatter_class=argparse.RawTextHelpFormatter,
    )

    # ---- utility flags ----
    p.add_argument("--list", action="store_true",
                   help="List available VISA resources and exit")

    # ---- instrument connections ----
    conn = p.add_argument_group("Instrument connections")
    conn.add_argument("--b1500", metavar="RESOURCE",
                      help="VISA resource string for B1500, e.g. GPIB0::17::INSTR")
    conn.add_argument("--pm",    metavar="RESOURCE",
                      help="VISA resource string for power meter, e.g. USB0::0x1313::0x8078::INSTR")

    # ---- sweep ----
    sweep = p.add_argument_group("Sweep parameters")
    sweep.add_argument("--mode",       choices=["iv", "vi"], default="iv",
                       help="iv: source V measure I (default); vi: source I measure V")
    sweep.add_argument("--smu",        type=int,   default=1,
                       help="B1500 SMU channel number (default: 1)")
    sweep.add_argument("--start",      type=float, default=0.0,
                       help="Sweep start value [V or A]")
    sweep.add_argument("--stop",       type=float, default=2.0,
                       help="Sweep stop value [V or A]")
    sweep.add_argument("--steps",      type=int,   default=21,
                       help="Number of sweep points (default: 21)")
    sweep.add_argument("--dwell",      type=float, default=0.1,
                       help="Dwell time per point in seconds (default: 0.1)")
    sweep.add_argument("--compliance", type=float, default=0.1,
                       help="Compliance limit [A for IV, V for VI] (default: 0.1)")
    sweep.add_argument("--two-dir",    action="store_true",
                       help="Perform forward + backward sweep")

    # ---- power meter ----
    pm = p.add_argument_group("Power meter")
    pm.add_argument("--no-pm",       action="store_true",
                    help="Disable power meter measurement")
    pm.add_argument("--wavelength",  type=float, default=850.0,
                    help="Wavelength for responsivity correction in nm (default: 850)")
    pm.add_argument("--pm-averages", type=int,   default=1,
                    help="Hardware averages per reading (default: 1)")

    # ---- rollover detection ----
    ro = p.add_argument_group("Rollover detection")
    ro.add_argument("--rollover",   action="store_true",
                    help="Enable rollover detection")
    ro.add_argument("--method",     choices=["rolling_avg", "ewma", "cusum", "regression"],
                    default="cusum",
                    help="Detection algorithm (default: cusum)")
    ro.add_argument("--threshold",  type=float, default=90.0,
                    help="Stop threshold as %% of peak power (default: 90)")
    ro.add_argument("--window",     type=int,   default=5,
                    help="Rolling-window / EWMA warm-up size (default: 5)")
    ro.add_argument("--alpha",      type=float, default=0.30,
                    help="EWMA decay factor α ∈ (0, 1] (default: 0.30)")
    ro.add_argument("--cusum-slack",type=float, default=1.0,
                    help="CUSUM slack k as %% of peak power (default: 1.0)")
    ro.add_argument("--cusum-h",    type=float, default=0.50,
                    help="CUSUM decision interval H × peak power (default: 0.50)")

    # ---- output ----
    out = p.add_argument_group("Output")
    out.add_argument("--output",    default="results",
                     help="Output folder path (default: ./results)")
    out.add_argument("--name",      default="Device",
                     help="Device name prefix for CSV file (default: Device)")
    out.add_argument("--no-save",   action="store_true",
                     help="Disable automatic CSV saving")

    return p


def run_cli(args: argparse.Namespace) -> int:
    """Execute the headless sweep described by *args*.

    Returns
    -------
    int
        0 on success, non-zero on failure (suitable for ``sys.exit()``).
    """
    # Deferred imports so the module is importable without heavy dependencies
    from .b1500_controller       import B1500Controller
    from .config                 import SweepConfig
    from .engine                 import SynchronizedMeasurementEngine
    from .powermeter_controller  import ThorlabsPowerMeterController

    # ---- list resources only ----
    if args.list:
        b1500 = B1500Controller()
        print("Available VISA resources:")
        for r in b1500.list_all_resources():
            print(f"  {r}")
        return 0

    # ---- validate required arguments ----
    if not args.b1500 and not args.pm:
        print("Error: at least one of --b1500 or --pm must be specified.", file=sys.stderr)
        return 1

    # ---- connect instruments ----
    b1500_ctrl = B1500Controller()
    pm_ctrl    = ThorlabsPowerMeterController()

    if args.b1500:
        ok, msg = b1500_ctrl.connect(args.b1500)
        print(f"B1500:  {msg}")
        if not ok:
            return 2

    if args.pm and not args.no_pm:
        ok, msg = pm_ctrl.connect(args.pm)
        print(f"PowerM: {msg}")
        if not ok:
            return 3

    # ---- build config ----
    cfg = SweepConfig(
        smu               = args.smu,
        mode              = args.mode,
        start             = args.start,
        stop              = args.stop,
        steps             = args.steps,
        dwell_s           = args.dwell,
        two_direction     = args.two_dir,
        compliance        = args.compliance,
        enable_power_meter= not args.no_pm,
        power_wavelength_nm = args.wavelength,
        power_averages    = args.pm_averages,
        enable_rollover   = args.rollover,
        rollover_method   = args.method,
        rollover_threshold= args.threshold / 100.0,
        rollover_window   = args.window,
        rollover_alpha    = args.alpha,
        cusum_slack       = args.cusum_slack / 100.0,
        cusum_h           = args.cusum_h,
        output_folder     = args.output,
        device_name       = args.name,
        autosave          = not args.no_save,
    )

    # ---- run sweep ----
    engine = SynchronizedMeasurementEngine(b1500_ctrl, pm_ctrl, cfg)
    engine.on_log = print
    data = engine.run()
    r    = engine.rollover_result

    # ---- print summary ----
    print()
    print("=" * 60)
    print(f"SWEEP COMPLETE  —  {len(data)} points")
    print(f"Stop reason     : {r.stop_reason}")
    if r.peak_point_index >= 0:
        print(f"Peak power      : {r.peak_power:.6e} W")
        print(f"  at Voltage    : {r.peak_voltage:.6f} V")
        print(f"  at Current    : {r.peak_current:.6e} A")
        print(f"  at point      : {r.peak_point_index + 1}/{len(data)}")
    print("=" * 60)

    # ---- cleanup ----
    b1500_ctrl.disconnect()
    pm_ctrl.disconnect()
    return 0
