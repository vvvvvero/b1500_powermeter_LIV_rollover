#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
b1500_powermeter_rollover
=========================
Python package for synchronized B1500 + Thorlabs power-meter IV sweeps
with four-algorithm rollover detection.

Quickstart
----------
    from b1500_powermeter_rollover import (
        SweepConfig,
        B1500Controller,
        ThorlabsPowerMeterController,
        RolloverDetector,
        SynchronizedMeasurementEngine,
    )

    b  = B1500Controller()
    pm = ThorlabsPowerMeterController()
    b.connect("GPIB0::17::INSTR")
    pm.connect("USB0::0x1313::0x8078::INSTR")

    cfg = SweepConfig(
        mode="iv", start=0, stop=2.5, steps=26,
        enable_rollover=True, rollover_method="cusum",
    )
    eng = SynchronizedMeasurementEngine(b, pm, cfg)
    eng.on_log = print
    data = eng.run()

GUI launch (PyQt5 required)
---------------------------
    python -m b1500_powermeter_rollover        # opens the GUI
    python -m b1500_powermeter_rollover --help  # CLI help

© Veronica Gao ZHan  –  May 2026
"""

from .b1500_controller      import B1500Controller
from .config                import MeasurementPoint, RolloverResult, SweepConfig
from .engine                import SynchronizedMeasurementEngine
from .powermeter_controller import ThorlabsPowerMeterController
from .rollover_detector     import RolloverDetector

__version__ = "1.0.0"
__author__  = "Veronica Gao ZHan"

__all__ = [
    # Data classes
    "SweepConfig",
    "MeasurementPoint",
    "RolloverResult",
    # Instrument drivers
    "B1500Controller",
    "ThorlabsPowerMeterController",
    # Algorithm
    "RolloverDetector",
    # Engine
    "SynchronizedMeasurementEngine",
    # Metadata
    "__version__",
    "__author__",
]
