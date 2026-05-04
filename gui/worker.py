#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
gui/worker.py
=============
QThread wrapper around :class:`~b1500_powermeter_rollover.engine.SynchronizedMeasurementEngine`.

The worker bridges the engine's callback hooks to Qt signals so that
GUI widgets can be updated safely from the main thread.

Usage (inside a QMainWindow)
----------------------------
    from b1500_powermeter_rollover.engine import SynchronizedMeasurementEngine
    from b1500_powermeter_rollover.gui.worker import MeasurementWorker

    engine = SynchronizedMeasurementEngine(b1500, power_meter, config)
    worker = MeasurementWorker(engine)
    worker.point_complete.connect(my_slot)
    worker.log_message.connect(my_log_slot)
    worker.rollover_detected.connect(my_result_slot)
    worker.finished_signal.connect(my_done_slot)
    worker.start()   # runs engine.run() in a background thread

© Veronica Gao ZHan  –  May 2026
"""

from __future__ import annotations

from PyQt5.QtCore import QThread, pyqtSignal

from ..engine import SynchronizedMeasurementEngine


class MeasurementWorker(QThread):
    """Background thread that runs :class:`SynchronizedMeasurementEngine.run`.

    Signals
    -------
    point_complete(object)
        Emitted after every sweep step with the :class:`~b1500_powermeter_rollover.config.MeasurementPoint`.
    progress(int, int)
        Emitted with (current_step, total_steps) after every step.
    log_message(str)
        Emitted for every engine log line.
    rollover_detected(object)
        Emitted once when the sweep ends with the :class:`~b1500_powermeter_rollover.config.RolloverResult`.
    finished_signal()
        Emitted after :meth:`~SynchronizedMeasurementEngine.run` returns.
    """

    point_complete    = pyqtSignal(object)
    progress          = pyqtSignal(int, int)
    log_message       = pyqtSignal(str)
    rollover_detected = pyqtSignal(object)
    finished_signal   = pyqtSignal()

    def __init__(self, engine: SynchronizedMeasurementEngine) -> None:
        super().__init__()
        self.engine = engine
        # Wire engine callbacks → Qt signals
        self.engine.on_point_complete    = lambda p: self.point_complete.emit(p)
        self.engine.on_progress          = lambda c, t: self.progress.emit(c, t)
        self.engine.on_log               = lambda m: self.log_message.emit(m)
        self.engine.on_rollover_detected = lambda r: self.rollover_detected.emit(r)

    def run(self) -> None:
        """Called by Qt in the worker thread; runs the measurement engine."""
        self.engine.run()
        self.finished_signal.emit()
