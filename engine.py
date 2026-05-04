#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
engine.py
=========
Synchronised B1500 + power-meter sweep with optional rollover detection.

The engine is GUI-agnostic: it communicates results exclusively through
callback hooks so it can be driven by a GUI thread, a CLI script, a
Jupyter notebook, or automated tests.

Standalone (headless) usage
---------------------------
    from b1500_powermeter_rollover.engine import SynchronizedMeasurementEngine
    from b1500_powermeter_rollover.config import SweepConfig
    from b1500_powermeter_rollover.b1500_controller import B1500Controller
    from b1500_powermeter_rollover.powermeter_controller import ThorlabsPowerMeterController

    b    = B1500Controller()
    pm   = ThorlabsPowerMeterController()
    b.connect("GPIB0::17::INSTR")
    pm.connect("USB0::0x1313::0x8078::INSTR")

    cfg  = SweepConfig(mode="iv", start=0, stop=2.5, steps=26,
                       enable_rollover=True, rollover_method="cusum")
    eng  = SynchronizedMeasurementEngine(b, pm, cfg)
    eng.on_log = print          # or any callable(str)

    data   = eng.run()          # blocks until sweep finishes
    result = eng.rollover_result
    print(f"Peak power {result.peak_power:.4e} W at {result.peak_voltage:.4f} V")

Callbacks
---------
on_point_complete(MeasurementPoint)   – called after every sweep step
on_progress(current_int, total_int)   – progress counter
on_log(str)                           – timestamped log line
on_rollover_detected(RolloverResult)  – called once when sweep ends

© Veronica Gao ZHan  –  May 2026
"""

from __future__ import annotations

import csv
import time
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from .b1500_controller       import B1500Controller
from .config                 import MeasurementPoint, RolloverResult, SweepConfig
from .powermeter_controller  import ThorlabsPowerMeterController
from .rollover_detector      import RolloverDetector


class SynchronizedMeasurementEngine:
    """Orchestrates a B1500 IV sweep synchronized with a Thorlabs power meter.

    The sweep loop:
    1. For each *setpoint* in :attr:`SweepConfig.setpoints`:
       a. Set B1500 source value and read (voltage, current).
       b. Read optical power from the power meter.
       c. Feed power to the rollover detector.
       d. Check compliance limit.
       e. Fire callbacks and, if required, stop early.
    2. Zero the B1500 output.
    3. Write results to CSV (when *autosave* is True).
    4. Fire ``on_rollover_detected`` with the final :class:`RolloverResult`.
    """

    def __init__(
        self,
        b1500:       B1500Controller,
        power_meter: ThorlabsPowerMeterController,
        config:      SweepConfig,
    ) -> None:
        self.b1500       = b1500
        self.power_meter = power_meter
        self.config      = config

        self.data:             List[MeasurementPoint] = []
        self.running:          bool                   = False
        self.stop_requested:   bool                   = False
        self.rollover_result:  RolloverResult         = RolloverResult()

        # --------------- Callback hooks ---------------
        self.on_point_complete:    Optional[Callable[[MeasurementPoint], None]] = None
        self.on_progress:          Optional[Callable[[int, int], None]]         = None
        self.on_log:               Optional[Callable[[str], None]]              = None
        self.on_rollover_detected: Optional[Callable[[RolloverResult], None]]   = None

    # ------------------------------------------------------------------
    # Public control
    # ------------------------------------------------------------------

    def stop(self) -> None:
        """Request an early stop.  The sweep finishes the current step first."""
        self.stop_requested = True

    # ------------------------------------------------------------------
    # Main sweep
    # ------------------------------------------------------------------

    def run(self) -> List[MeasurementPoint]:
        """Execute the full sweep and return the list of :class:`MeasurementPoint`.

        Blocks until the sweep is complete (or stopped).  Safe to call from
        a background thread.
        """
        self.running       = True
        self.stop_requested = False
        self.data          = []
        self.rollover_result = RolloverResult()

        setpoints    = self.config.setpoints
        total_points = len(setpoints)
        start_time   = time.time()
        stop_reason  = "sweep_complete"

        # Peak tracking
        peak_power   = -1.0
        peak_voltage = 0.0
        peak_current = 0.0
        peak_index   = -1

        detector = RolloverDetector(self.config)

        self._log(f"Starting sweep: {total_points} points")
        self._log(
            f"Mode: {'V→I' if self.config.mode == 'iv' else 'I→V'}, "
            f"range: {self.config.start} → {self.config.stop}"
        )
        if self.config.enable_rollover:
            self._log(
                f"Rollover detection ON — method={self.config.rollover_method}, "
                f"window={self.config.rollover_window}, "
                f"threshold={self.config.rollover_threshold * 100:.0f}% of peak"
            )

        # --- Configure power meter ---
        if self.power_meter.connected and self.config.enable_power_meter:
            try:
                self.power_meter.configure(
                    wavelength_nm=self.config.power_wavelength_nm,
                    auto_range=(self.config.power_range == "AUTO"),
                    averages=self.config.power_averages,
                )
                self._log(f"Power meter configured: λ={self.config.power_wavelength_nm} nm")
            except Exception as e:
                self._log(f"Power meter configuration error: {e}")

        # --- Validate B1500 connection ---
        if self.b1500.connected:
            ok, verify_msg = self.b1500.verify_connection()
            if not ok:
                self._log(f"B1500 connection invalid: {verify_msg}. Aborting.")
                self.running = False
                return self.data
            self._log("B1500 connection verified")
            try:
                self.b1500.configure_sweep(self.config)
                self._log("B1500 configured")
            except Exception as e:
                self._log(f"B1500 configuration error: {e}")
                self.running = False
                return self.data

        try:
            for idx, setpoint in enumerate(setpoints):
                if self.stop_requested:
                    self._log("Sweep stopped by user")
                    stop_reason = "user_stop"
                    break

                timestamp     = time.time()
                relative_time = timestamp - start_time

                # ---- IV measurement ----
                if self.b1500.connected:
                    voltage, current = self.b1500.set_bias_and_measure(
                        self.config.smu, setpoint, self.config
                    )
                else:
                    voltage = setpoint if self.config.mode == "iv" else 0.0
                    current = 0.0     if self.config.mode == "iv" else setpoint

                # ---- Optical power measurement ----
                if self.power_meter.connected and self.config.enable_power_meter:
                    power, status = self.power_meter.measure_power()
                else:
                    power  = 0.0
                    status = "Power meter not connected"

                # ---- Record point ----
                point = MeasurementPoint(
                    point_index=idx,
                    timestamp=timestamp,
                    relative_time=relative_time,
                    setpoint=setpoint,
                    voltage=voltage,
                    current=current,
                    optical_power=power,
                    status=status,
                )
                self.data.append(point)

                # ---- Update peak ----
                if power > peak_power:
                    peak_power   = power
                    peak_voltage = voltage
                    peak_current = current
                    peak_index   = idx

                # ---- Rollover detection ----
                rollover_triggered                  = False
                rollover_info: Dict[str, Any]       = {}
                if self.config.enable_rollover:
                    rollover_triggered, rollover_info = detector.update(power, peak_power)

                # ---- Compliance check ----
                compliance_hit = self._check_compliance(voltage, current)
                if compliance_hit:
                    point.status = "COMPLIANCE"

                # ---- Log ----
                flags = (
                    (" [ROLLOVER]"    if rollover_triggered else "")
                    + (" [COMPLIANCE]" if compliance_hit      else "")
                )
                self._log(
                    f"Point {idx + 1}/{total_points}: "
                    f"V={voltage:.4f} V  I={current:.4e} A  "
                    f"P={power:.4e} W{flags}"
                )

                # ---- Callbacks ----
                if self.on_point_complete:
                    self.on_point_complete(point)
                if self.on_progress:
                    self.on_progress(idx + 1, total_points)

                # ---- Early stop: rollover ----
                if rollover_triggered:
                    stop_reason = "rollover"
                    self._log(
                        self._format_rollover_log(
                            idx + 1, total_points, rollover_info, peak_power
                        )
                    )
                    break

                # ---- Early stop: compliance ----
                if compliance_hit and self.config.stop_on_compliance:
                    stop_reason = "compliance"
                    self._log(
                        f"Compliance limit reached at point {idx + 1}. Stopping."
                    )
                    break

        except Exception as e:
            self._log(f"Sweep error: {e}")
            traceback.print_exc()
        finally:
            self.running = False

        # ------------------------------------------------------------------
        # Post-sweep
        # ------------------------------------------------------------------
        self.rollover_result = RolloverResult(
            detected        = (stop_reason == "rollover"),
            peak_power      = max(peak_power, 0.0),
            peak_voltage    = peak_voltage,
            peak_current    = peak_current,
            peak_point_index = peak_index,
            stop_reason     = stop_reason,
        )

        self._log("=" * 60)
        self._log(f"Sweep finished — {stop_reason.upper()}")
        if peak_index >= 0:
            self._log(f"  Peak power  : {peak_power:.6e} W")
            self._log(f"  at Voltage  : {peak_voltage:.6f} V")
            self._log(f"  at Current  : {peak_current:.6e} A")
            self._log(f"  at index    : {peak_index + 1}/{total_points}")
        self._log("=" * 60)

        if self.on_rollover_detected:
            self.on_rollover_detected(self.rollover_result)

        if self.b1500.connected:
            self.b1500.output_off(self.config.smu)

        self.save_csv()
        self._log(f"Done: {len(self.data)} points recorded.")
        return self.data

    # ------------------------------------------------------------------
    # CSV export
    # ------------------------------------------------------------------

    def save_csv(self) -> Optional[Path]:
        """Write measurement data and rollover summary to a timestamped CSV.

        Returns the path of the saved file, or ``None`` if autosave is
        disabled or there is no data.
        """
        if not self.data:
            return None
        if not self.config.autosave:
            self._log("Autosave disabled – data not saved automatically")
            return None

        output_path = Path(self.config.output_folder)
        if not output_path.is_absolute():
            output_path = Path(__file__).parent.parent / self.config.output_folder
        output_path.mkdir(parents=True, exist_ok=True)

        ts       = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{self.config.device_name}_iv_power_rollover_{ts}.csv"
        filepath = output_path / filename

        with open(filepath, "w", newline="") as f:
            w = csv.writer(f)

            # Metadata header
            w.writerow(["# Rollover Detection Result"])
            r = self.rollover_result
            if r.peak_point_index >= 0:
                w.writerow(["# Peak Power (W)",   f"{r.peak_power:.6e}"])
                w.writerow(["# Peak Voltage (V)",  f"{r.peak_voltage:.6f}"])
                w.writerow(["# Peak Current (A)",  f"{r.peak_current:.6e}"])
                w.writerow(["# Stop Reason",        r.stop_reason])
            w.writerow([])  # blank separator

            # Column header
            w.writerow([
                "Point", "Timestamp", "Relative_Time_s",
                "Setpoint", "Voltage_V", "Current_A",
                "Optical_Power_W", "Status",
            ])

            # Data rows
            for pt in self.data:
                w.writerow([
                    pt.point_index,
                    datetime.fromtimestamp(pt.timestamp).isoformat(),
                    f"{pt.relative_time:.6f}",
                    f"{pt.setpoint:.6e}",
                    f"{pt.voltage:.6e}",
                    f"{pt.current:.6e}",
                    f"{pt.optical_power:.6e}",
                    pt.status,
                ])

        self._log(f"Data saved: {filepath}")
        return filepath

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _log(self, message: str) -> None:
        ts  = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        msg = f"[{ts}] {message}"
        print(msg)
        if self.on_log:
            self.on_log(msg)

    def _check_compliance(self, voltage: float, current: float) -> bool:
        tol        = self.config.compliance_tolerance
        compliance = abs(self.config.compliance)
        if self.config.mode == "iv":
            return abs(current) >= compliance * tol
        return abs(voltage) >= compliance * tol

    @staticmethod
    def _format_rollover_log(
        idx: int,
        total: int,
        info: Dict[str, Any],
        peak_power: float,
    ) -> str:
        method = info.get("method", "?")
        if "window_mean" in info:
            detail = f"window_mean={info['window_mean']:.4e} W"
        elif "ewma" in info:
            detail = f"EWMA={info['ewma']:.4e} W"
        elif "S_neg" in info:
            detail = f"S_neg={info['S_neg']:.4e}, H={info['H']:.4e}"
        elif "slope" in info:
            detail = (
                f"slope={info['slope']:.4e} W/pt, "
                f"mean={info['regression_mean']:.4e} W"
            )
        else:
            detail = ""
        return (
            f"Rollover [{method}] at point {idx}/{total}: {detail} "
            f"(peak={peak_power:.4e} W)"
        )
