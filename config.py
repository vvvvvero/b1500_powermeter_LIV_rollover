#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
config.py
=========
Pure data classes shared across all modules.

Classes
-------
SweepConfig         – all measurement / rollover parameters
MeasurementPoint    – one synchronised IV + power sample
RolloverResult      – summary produced after a sweep

These classes have NO external dependencies (only stdlib) so any module
in the project – or any third-party script – can import them in isolation:

    from b1500_powermeter_rollover.config import SweepConfig, RolloverResult

© Veronica Gao ZHan  –  May 2026
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional


# =============================================================================
# SweepConfig
# =============================================================================

@dataclass
class SweepConfig:
    """Full configuration for a B1500 + power-meter IV sweep with rollover detection.

    Usage example
    -------------
    >>> cfg = SweepConfig(mode="iv", start=0.0, stop=2.5, steps=26, dwell_s=0.1)
    >>> cfg.setpoints            # list of 26 voltages from 0 to 2.5 V
    >>> cfg.enable_rollover = True
    >>> cfg.rollover_method = "cusum"
    """

    # ------------------------------------------------------------------
    # B1500 settings
    # ------------------------------------------------------------------
    smu: int = 1
    """SMU channel number (1–10)."""

    mode: str = "iv"
    """Sweep direction: ``"iv"`` → source Voltage, measure Current;
    ``"vi"`` → source Current, measure Voltage."""

    start: float = 0.0
    """First setpoint value (V or A depending on *mode*)."""

    stop: float = 1.0
    """Last setpoint value."""

    steps: int = 11
    """Number of setpoints (including start and stop)."""

    dwell_s: float = 0.1
    """Settling time (seconds) between setting the source and measuring."""

    two_direction: bool = False
    """If True, sweep forward then back (start→stop→start)."""

    compliance: float = 0.1
    """Current compliance (A) in IV mode or voltage compliance (V) in VI mode."""

    meas_range: Optional[float] = None
    """Measurement range code; ``None`` means auto-range (code 0)."""

    integration_time: str = "MEDIUM"
    """Integration-time preset or encoded string.
    Plain values: ``"SHORT"``, ``"MEDIUM"``, ``"LONG"``.
    Encoded values: ``"AUTO_SHORT_1"``, ``"PLC_2"``, ``"MANUAL_0.001"``."""

    # ------------------------------------------------------------------
    # Power-meter settings
    # ------------------------------------------------------------------
    enable_power_meter: bool = True
    """If False the power-meter step is skipped (optical_power = 0)."""

    power_wavelength_nm: float = 850.0
    """Centre wavelength for the power-meter responsivity correction (nm)."""

    power_range: str = "AUTO"
    """Power range: ``"AUTO"`` or a string like ``"1mW"`` / ``"10mW"``."""

    power_averages: int = 1
    """Number of hardware averages per power reading."""

    # ------------------------------------------------------------------
    # Rollover detection
    # ------------------------------------------------------------------
    enable_rollover: bool = True
    """Master enable for the rollover-stop feature."""

    rollover_window: int = 5
    """Number of recent readings used by the rolling-average, EWMA, and
    regression methods.  Larger → more noise-tolerant, slower reaction."""

    rollover_threshold: float = 0.90
    """Fraction of peak power at which to declare rollover.
    ``0.90`` = stop when power has dropped to 90 % of its maximum value."""

    rollover_method: str = "cusum"
    """Detection algorithm:

    ``"rolling_avg"``  – rolling-window mean < threshold × peak (original).
    ``"ewma"``         – Exponentially-Weighted Moving Average (faster).
    ``"cusum"``        – CUSUM change-point detector (fastest, ~1–2 pts).
    ``"regression"``   – sklearn LinearRegression + online SGDRegressor.
    """

    rollover_alpha: float = 0.3
    """EWMA decay factor α ∈ (0, 1].  Smaller → smoother & slower.
    Only used when *rollover_method* is ``"ewma"``."""

    cusum_slack: float = 0.01
    """CUSUM per-step noise allowance as a fraction of peak power (e.g. 0.01 = 1 %).
    Only used when *rollover_method* is ``"cusum"``."""

    cusum_h: float = 0.5
    """CUSUM decision interval as a multiple of peak power.
    Typical range 0.2–1.0.  Only used when *rollover_method* is ``"cusum"``."""

    # ------------------------------------------------------------------
    # Output settings
    # ------------------------------------------------------------------
    output_folder: str = "results"
    """Folder for CSV output (relative to the script or absolute)."""

    output_file: str = "iv_power_rollover.csv"
    """Base filename (timestamp is appended automatically)."""

    device_name: str = "Device_001"
    """Device label prepended to output filenames."""

    autosave: bool = True
    """If True the engine auto-saves a CSV after every sweep."""

    # ------------------------------------------------------------------
    # Compliance control
    # ------------------------------------------------------------------
    stop_on_compliance: bool = True
    """Stop the sweep when the compliance limit is reached."""

    compliance_tolerance: float = 0.99
    """A measured value ≥ ``compliance_tolerance × |compliance|`` counts as
    compliance hit (allows for small ADC offsets)."""

    # ------------------------------------------------------------------
    # Derived properties
    # ------------------------------------------------------------------

    @property
    def setpoints(self) -> List[float]:
        """Ordered list of source setpoints for this sweep."""
        if self.steps < 2:
            return [self.start]
        fwd = [
            self.start + i * (self.stop - self.start) / (self.steps - 1)
            for i in range(self.steps)
        ]
        if not self.two_direction:
            return fwd
        return fwd + list(reversed(fwd[:-1]))

    @property
    def source_quantity(self) -> str:
        """``"VOLT"`` for IV mode, ``"CURR"`` for VI mode."""
        return "VOLT" if self.mode == "iv" else "CURR"

    @property
    def sense_quantity(self) -> str:
        """``"CURR"`` for IV mode, ``"VOLT"`` for VI mode."""
        return "CURR" if self.mode == "iv" else "VOLT"


# =============================================================================
# MeasurementPoint
# =============================================================================

@dataclass
class MeasurementPoint:
    """A single synchronised IV + optical-power measurement sample.

    Produced by :class:`~b1500_powermeter_rollover.engine.SynchronizedMeasurementEngine`
    at every sweep step and emitted to GUI / CLI callbacks.
    """

    point_index: int
    """Zero-based position within the sweep."""

    timestamp: float
    """Absolute UNIX timestamp of the measurement."""

    relative_time: float
    """Seconds since the start of the sweep."""

    setpoint: float
    """Commanded source value (V or A)."""

    voltage: float
    """Measured (or sourced) voltage (V)."""

    current: float
    """Measured (or sourced) current (A)."""

    optical_power: float
    """Measured optical power (W); 0.0 if power meter is disabled."""

    power_unit: str = "W"
    """Unit string for ``optical_power``."""

    status: str = "OK"
    """``"OK"``, ``"COMPLIANCE"``, or an error description."""


# =============================================================================
# RolloverResult
# =============================================================================

@dataclass
class RolloverResult:
    """Summary of a completed sweep, always produced regardless of stop reason.

    All fields are safe to inspect after
    :meth:`~b1500_powermeter_rollover.engine.SynchronizedMeasurementEngine.run`
    returns.
    """

    detected: bool = False
    """True if the sweep was stopped by the rollover detector."""

    peak_power: float = 0.0
    """Highest single optical-power reading observed during the sweep (W)."""

    peak_voltage: float = 0.0
    """Voltage at the peak-power point (V)."""

    peak_current: float = 0.0
    """Current at the peak-power point (A)."""

    peak_point_index: int = -1
    """Zero-based index of the peak-power point in the data list; ``-1`` if no
    valid peak was found."""

    stop_reason: str = ""
    """One of: ``"rollover"``, ``"compliance"``, ``"user_stop"``,
    ``"sweep_complete"``."""
