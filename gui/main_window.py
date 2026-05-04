#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
gui/main_window.py
==================
Full-featured PyQt5 QMainWindow: scrollable control panel on the left,
live four-subplot matplotlib canvas on the right.

Stand-alone launch
------------------
    from b1500_powermeter_rollover.gui.main_window import SynchronizedMeasurementGUI
    from PyQt5.QtWidgets import QApplication
    import sys

    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    win = SynchronizedMeasurementGUI()
    win.show()
    sys.exit(app.exec_())

© Veronica Gao ZHan  –  May 2026
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional, Tuple

import matplotlib
matplotlib.use("Qt5Agg")
import matplotlib.pyplot as plt
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure
import numpy as np

from PyQt5.QtCore  import Qt
from PyQt5.QtGui   import QFont
from PyQt5.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog,
    QGridLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QMainWindow,
    QMessageBox, QProgressBar, QPushButton, QScrollArea, QSpinBox,
    QStatusBar, QTextEdit, QVBoxLayout, QWidget,
)

from ..b1500_controller      import B1500Controller
from ..config                import MeasurementPoint, RolloverResult, SweepConfig
from ..engine                import SynchronizedMeasurementEngine
from ..powermeter_controller import ThorlabsPowerMeterController
from .worker                 import MeasurementWorker


class SynchronizedMeasurementGUI(QMainWindow):
    """B1500 + Power Meter control / live-plot window with rollover detection.

    The window is divided into:
    * **Left scroll panel** – all configuration groups + control buttons + log
    * **Right panel** – 2×2 matplotlib subplot canvas (I-V linear, I-V log, L-I, P-V)
    """

    def __init__(self) -> None:
        super().__init__()
        self.b1500       = B1500Controller()
        self.power_meter = ThorlabsPowerMeterController()
        self.worker:     Optional[MeasurementWorker] = None

        self.setWindowTitle(
            "B1500 + Power Meter \u2013 Rollover Detection | \u00A9 Veronica Gao ZHan"
        )
        self.setMinimumSize(1400, 950)
        self._setup_ui()
        self.refresh_resources()

    # ==================================================================
    # UI construction
    # ==================================================================

    def _setup_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QHBoxLayout(central)

        # ---- left scrollable control panel ----
        left_panel  = QWidget()
        left_layout = QVBoxLayout(left_panel)
        left_panel.setMinimumWidth(460)

        left_layout.addWidget(self._build_device_group())
        left_layout.addWidget(self._build_sweep_group())
        left_layout.addWidget(self._build_pm_group())
        left_layout.addWidget(self._build_rollover_group())
        left_layout.addWidget(self._build_output_group())
        left_layout.addLayout(self._build_control_buttons())
        self.progress_bar = QProgressBar()
        self.progress_bar.setValue(0)
        left_layout.addWidget(self.progress_bar)
        left_layout.addWidget(self._build_log_group())
        left_layout.addStretch()

        left_scroll = QScrollArea()
        left_scroll.setWidget(left_panel)
        left_scroll.setWidgetResizable(True)
        left_scroll.setFixedWidth(500)
        left_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        left_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        main_layout.addWidget(left_scroll)

        # ---- right plot panel ----
        right_panel  = QWidget()
        right_layout = QVBoxLayout(right_panel)

        self.figure = Figure(figsize=(10, 8))
        self.canvas = FigureCanvas(self.figure)

        self.ax_iv     = self.figure.add_subplot(2, 2, 1)
        self.ax_iv_log = self.figure.add_subplot(2, 2, 2)
        self.ax_li     = self.figure.add_subplot(2, 2, 3)
        self.ax_pv     = self.figure.add_subplot(2, 2, 4)
        self._init_axes()

        right_layout.addWidget(self.canvas)
        main_layout.addWidget(right_panel, stretch=2)

        # ---- status bar ----
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self.status_bar.addPermanentWidget(
            QLabel("  B1500 + Power Meter \u2013 Rollover Detection | \u00A9 Veronica Gao ZHan  ")
        )
        self.status_bar.showMessage("Ready")

        # ---- plot data buffers ----
        self.plot_voltages: list = []
        self.plot_currents: list = []
        self.plot_powers:   list = []
        self._peak_marker_li: Optional[Tuple[float, float]] = None
        self._peak_marker_pv: Optional[Tuple[float, float]] = None

    # ------------------------------------------------------------------
    # Group builders
    # ------------------------------------------------------------------

    def _build_device_group(self) -> QGroupBox:
        g      = QGroupBox("Device Connection")
        layout = QGridLayout(g)

        layout.addWidget(QLabel("B1500:"), 0, 0)
        self.combo_b1500 = QComboBox()
        self.combo_b1500.setMinimumWidth(200)
        layout.addWidget(self.combo_b1500, 0, 1)
        self.btn_connect_b1500 = QPushButton("Connect")
        self.btn_connect_b1500.clicked.connect(self.connect_b1500)
        layout.addWidget(self.btn_connect_b1500, 0, 2)
        self.label_b1500_status = QLabel("Not connected")
        self.label_b1500_status.setStyleSheet("color: red;")
        layout.addWidget(self.label_b1500_status, 1, 0, 1, 3)

        layout.addWidget(QLabel("Power Meter:"), 2, 0)
        self.combo_power_meter = QComboBox()
        self.combo_power_meter.setMinimumWidth(200)
        layout.addWidget(self.combo_power_meter, 2, 1)
        self.btn_connect_pm = QPushButton("Connect")
        self.btn_connect_pm.clicked.connect(self.connect_power_meter)
        layout.addWidget(self.btn_connect_pm, 2, 2)
        self.label_pm_status = QLabel("Not connected")
        self.label_pm_status.setStyleSheet("color: red;")
        layout.addWidget(self.label_pm_status, 3, 0, 1, 3)

        self.btn_refresh = QPushButton("Refresh Devices")
        self.btn_refresh.clicked.connect(self.refresh_resources)
        layout.addWidget(self.btn_refresh, 4, 0, 1, 3)
        return g

    def _build_sweep_group(self) -> QGroupBox:
        g      = QGroupBox("B1500 IV Sweep Configuration")
        layout = QGridLayout(g)

        layout.addWidget(QLabel("SMU Channel:"), 0, 0)
        self.spin_smu = QSpinBox()
        self.spin_smu.setRange(1, 10)
        self.spin_smu.setValue(1)
        layout.addWidget(self.spin_smu, 0, 1)

        layout.addWidget(QLabel("Mode:"), 0, 2)
        self.combo_mode = QComboBox()
        self.combo_mode.addItems(["IV (Source V, Measure I)", "VI (Source I, Measure V)"])
        layout.addWidget(self.combo_mode, 0, 3)

        layout.addWidget(QLabel("Start:"), 1, 0)
        self.spin_start = QDoubleSpinBox()
        self.spin_start.setRange(-200, 200)
        self.spin_start.setDecimals(4)
        self.spin_start.setValue(0)
        layout.addWidget(self.spin_start, 1, 1)

        layout.addWidget(QLabel("Stop:"), 1, 2)
        self.spin_stop = QDoubleSpinBox()
        self.spin_stop.setRange(-200, 200)
        self.spin_stop.setDecimals(4)
        self.spin_stop.setValue(2.0)
        layout.addWidget(self.spin_stop, 1, 3)

        layout.addWidget(QLabel("Steps:"), 2, 0)
        self.spin_steps = QSpinBox()
        self.spin_steps.setRange(2, 10001)
        self.spin_steps.setValue(21)
        layout.addWidget(self.spin_steps, 2, 1)

        layout.addWidget(QLabel("Dwell (s):"), 2, 2)
        self.spin_dwell = QDoubleSpinBox()
        self.spin_dwell.setRange(0, 10)
        self.spin_dwell.setDecimals(3)
        self.spin_dwell.setValue(0.1)
        layout.addWidget(self.spin_dwell, 2, 3)

        layout.addWidget(QLabel("Compliance:"), 3, 0)
        self.spin_compliance = QDoubleSpinBox()
        self.spin_compliance.setRange(-200, 200)
        self.spin_compliance.setDecimals(6)
        self.spin_compliance.setValue(0.1)
        self.spin_compliance.setSuffix(" A/V")
        layout.addWidget(self.spin_compliance, 3, 1)

        self.check_two_dir = QCheckBox("Two-Direction Sweep")
        layout.addWidget(self.check_two_dir, 3, 2)

        self.check_stop_compliance = QCheckBox("Stop on Compliance")
        self.check_stop_compliance.setChecked(True)
        layout.addWidget(self.check_stop_compliance, 3, 3)

        layout.addWidget(QLabel("Integration Mode:"), 4, 0)
        self.combo_integration_mode = QComboBox()
        self.combo_integration_mode.addItems(["Auto", "PLC", "Manual"])
        self.combo_integration_mode.currentIndexChanged.connect(self._on_integration_mode_changed)
        layout.addWidget(self.combo_integration_mode, 4, 1)

        layout.addWidget(QLabel("ADC Type:"), 4, 2)
        self.combo_adc_type = QComboBox()
        self.combo_adc_type.addItems(["High-Speed", "High-Resolution"])
        layout.addWidget(self.combo_adc_type, 4, 3)

        layout.addWidget(QLabel("N Value:"), 5, 0)
        self.spin_integration_n = QSpinBox()
        self.spin_integration_n.setRange(1, 1023)
        self.spin_integration_n.setValue(1)
        layout.addWidget(self.spin_integration_n, 5, 1)

        layout.addWidget(QLabel("Aperture (s):"), 5, 2)
        self.spin_aperture = QDoubleSpinBox()
        self.spin_aperture.setRange(0.00001, 10.0)
        self.spin_aperture.setDecimals(5)
        self.spin_aperture.setValue(0.001)
        self.spin_aperture.setEnabled(False)
        layout.addWidget(self.spin_aperture, 5, 3)
        return g

    def _build_pm_group(self) -> QGroupBox:
        g      = QGroupBox("Power Meter Configuration")
        layout = QGridLayout(g)

        self.check_enable_pm = QCheckBox("Enable Power Measurement")
        self.check_enable_pm.setChecked(True)
        layout.addWidget(self.check_enable_pm, 0, 0, 1, 2)

        layout.addWidget(QLabel("Wavelength (nm):"), 1, 0)
        self.spin_wavelength = QDoubleSpinBox()
        self.spin_wavelength.setRange(200, 2000)
        self.spin_wavelength.setValue(850)
        layout.addWidget(self.spin_wavelength, 1, 1)

        layout.addWidget(QLabel("Range:"), 1, 2)
        self.combo_pm_range = QComboBox()
        self.combo_pm_range.addItems(["AUTO", "1mW", "10mW", "40mW"])
        layout.addWidget(self.combo_pm_range, 1, 3)

        layout.addWidget(QLabel("Averages:"), 2, 0)
        self.spin_pm_avg = QSpinBox()
        self.spin_pm_avg.setRange(1, 1000)
        self.spin_pm_avg.setValue(1)
        layout.addWidget(self.spin_pm_avg, 2, 1)
        return g

    def _build_rollover_group(self) -> QGroupBox:
        g = QGroupBox("Rollover Detection")
        g.setStyleSheet("QGroupBox { font-weight: bold; }")
        layout = QGridLayout(g)

        # Row 0 – enable
        self.check_enable_rollover = QCheckBox("Enable Rollover Detection")
        self.check_enable_rollover.setChecked(True)
        layout.addWidget(self.check_enable_rollover, 0, 0, 1, 4)

        # Row 1 – window + threshold
        layout.addWidget(QLabel("Window (pts):"), 1, 0)
        self.spin_rollover_window = QSpinBox()
        self.spin_rollover_window.setRange(1, 50)
        self.spin_rollover_window.setValue(5)
        self.spin_rollover_window.setMinimumHeight(32)
        self.spin_rollover_window.setToolTip(
            "Number of recent readings used by rolling_avg / ewma / regression.\n"
            "CUSUM reacts after just 1-2 points and ignores this value."
        )
        layout.addWidget(self.spin_rollover_window, 1, 1)

        layout.addWidget(QLabel("Threshold (%):"), 1, 2)
        self.spin_rollover_threshold = QDoubleSpinBox()
        self.spin_rollover_threshold.setRange(50.0, 99.9)
        self.spin_rollover_threshold.setDecimals(1)
        self.spin_rollover_threshold.setValue(90.0)
        self.spin_rollover_threshold.setSuffix(" %")
        self.spin_rollover_threshold.setMinimumHeight(32)
        self.spin_rollover_threshold.setToolTip(
            "Stop when power drops to this fraction of peak.\n"
            "90 % → stop at 10 % rolloff.   85 % → allow 15 % before stopping."
        )
        layout.addWidget(self.spin_rollover_threshold, 1, 3)

        # Row 2 – method selector
        layout.addWidget(QLabel("Method:"), 2, 0)
        self.combo_rollover_method = QComboBox()
        self.combo_rollover_method.setMinimumHeight(32)
        self.combo_rollover_method.addItem("CUSUM (fastest, ~1-2 pts)",      "cusum")
        self.combo_rollover_method.addItem("EWMA  (faster, \u03b1-decay)",   "ewma")
        self.combo_rollover_method.addItem("Rolling Average (original)",     "rolling_avg")
        self.combo_rollover_method.addItem("Regression ML (sklearn/numpy)",  "regression")
        layout.addWidget(self.combo_rollover_method, 2, 1, 1, 3)

        # Row 3 – EWMA α / CUSUM H
        layout.addWidget(QLabel("EWMA \u03b1:"), 3, 0)
        self.spin_rollover_alpha = QDoubleSpinBox()
        self.spin_rollover_alpha.setRange(0.01, 1.0)
        self.spin_rollover_alpha.setDecimals(2)
        self.spin_rollover_alpha.setSingleStep(0.05)
        self.spin_rollover_alpha.setValue(0.30)
        self.spin_rollover_alpha.setMinimumHeight(32)
        layout.addWidget(self.spin_rollover_alpha, 3, 1)

        layout.addWidget(QLabel("CUSUM H \u00d7peak:"), 3, 2)
        self.spin_cusum_h = QDoubleSpinBox()
        self.spin_cusum_h.setRange(0.05, 5.0)
        self.spin_cusum_h.setDecimals(2)
        self.spin_cusum_h.setSingleStep(0.1)
        self.spin_cusum_h.setValue(0.50)
        self.spin_cusum_h.setMinimumHeight(32)
        layout.addWidget(self.spin_cusum_h, 3, 3)

        # Row 4 – CUSUM slack
        layout.addWidget(QLabel("CUSUM slack (%):"), 4, 0)
        self.spin_cusum_slack = QDoubleSpinBox()
        self.spin_cusum_slack.setRange(0.0, 20.0)
        self.spin_cusum_slack.setDecimals(2)
        self.spin_cusum_slack.setSingleStep(0.5)
        self.spin_cusum_slack.setValue(1.0)
        self.spin_cusum_slack.setSuffix(" %")
        self.spin_cusum_slack.setMinimumHeight(32)
        layout.addWidget(self.spin_cusum_slack, 4, 1)

        # Rows 5-6 – results
        layout.addWidget(QLabel("Peak Power:"), 5, 0)
        self.label_peak_power = QLabel("\u2014")
        self.label_peak_power.setStyleSheet("color: #0055cc; font-weight: bold;")
        layout.addWidget(self.label_peak_power, 5, 1)

        layout.addWidget(QLabel("at Voltage:"), 5, 2)
        self.label_peak_voltage = QLabel("\u2014")
        self.label_peak_voltage.setStyleSheet("color: #0055cc; font-weight: bold;")
        layout.addWidget(self.label_peak_voltage, 5, 3)

        layout.addWidget(QLabel("at Current:"), 6, 0)
        self.label_peak_current = QLabel("\u2014")
        self.label_peak_current.setStyleSheet("color: #0055cc; font-weight: bold;")
        layout.addWidget(self.label_peak_current, 6, 1)

        layout.addWidget(QLabel("Stop Reason:"), 6, 2)
        self.label_stop_reason = QLabel("\u2014")
        layout.addWidget(self.label_stop_reason, 6, 3)
        return g

    def _build_output_group(self) -> QGroupBox:
        g      = QGroupBox("Output Configuration")
        layout = QGridLayout(g)

        layout.addWidget(QLabel("Folder Path:"), 0, 0)
        self.edit_folder_path = QLineEdit(str(Path(__file__).parents[2] / "results"))
        layout.addWidget(self.edit_folder_path, 0, 1)
        btn_browse = QPushButton("Browse...")
        btn_browse.clicked.connect(self._browse_folder)
        layout.addWidget(btn_browse, 0, 2)

        layout.addWidget(QLabel("Device Name:"), 1, 0)
        self.edit_device_name = QLineEdit("Device_001")
        layout.addWidget(self.edit_device_name, 1, 1, 1, 2)

        lbl = QLabel("<DeviceName>_iv_power_rollover_<timestamp>.csv")
        lbl.setStyleSheet("color: gray; font-style: italic;")
        layout.addWidget(QLabel("File Pattern:"), 2, 0)
        layout.addWidget(lbl, 2, 1, 1, 2)

        self.check_autosave = QCheckBox("Enable Autosave")
        self.check_autosave.setChecked(True)
        layout.addWidget(self.check_autosave, 3, 0, 1, 3)
        return g

    def _build_control_buttons(self) -> QHBoxLayout:
        layout = QHBoxLayout()

        self.btn_start = QPushButton("\u25b6 Start Measurement")
        self.btn_start.setMinimumHeight(40)
        self.btn_start.setStyleSheet(
            "background-color: #4CAF50; color: white; font-weight: bold;"
        )
        self.btn_start.clicked.connect(self.start_measurement)
        layout.addWidget(self.btn_start)

        self.btn_stop = QPushButton("\u25a0 Stop")
        self.btn_stop.setMinimumHeight(40)
        self.btn_stop.setEnabled(False)
        self.btn_stop.clicked.connect(self.stop_measurement)
        layout.addWidget(self.btn_stop)
        return layout

    def _build_log_group(self) -> QGroupBox:
        g      = QGroupBox("Log")
        layout = QVBoxLayout(g)
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setMaximumHeight(180)
        layout.addWidget(self.log_text)
        return g

    # ------------------------------------------------------------------
    # Axes initialisation
    # ------------------------------------------------------------------

    def _init_axes(self) -> None:
        self.ax_iv.set_xlabel("Voltage (V)")
        self.ax_iv.set_ylabel("Current (A)")
        self.ax_iv.set_title("I-V Characteristic")
        self.ax_iv.grid(True, alpha=0.3)

        self.ax_iv_log.set_xlabel("Voltage (V)")
        self.ax_iv_log.set_ylabel("|Current| (A)")
        self.ax_iv_log.set_title("I-V Characteristic (Log Scale)")
        self.ax_iv_log.set_yscale("log")
        self.ax_iv_log.grid(True, alpha=0.3)

        self.ax_li.set_xlabel("Current (A)")
        self.ax_li.set_ylabel("Optical Power (W)")
        self.ax_li.set_title("L-I Characteristic")
        self.ax_li.grid(True, alpha=0.3)

        self.ax_pv.set_xlabel("Voltage (V)")
        self.ax_pv.set_ylabel("Optical Power (W)")
        self.ax_pv.set_title("P-V Characteristic")
        self.ax_pv.grid(True, alpha=0.3)

        self.figure.tight_layout()

    # ==================================================================
    # Logging
    # ==================================================================

    def log(self, message: str) -> None:
        self.log_text.append(message)
        self.log_text.verticalScrollBar().setValue(
            self.log_text.verticalScrollBar().maximum()
        )

    # ==================================================================
    # Device management
    # ==================================================================

    def refresh_resources(self) -> None:
        self.combo_b1500.clear()
        self.combo_power_meter.clear()
        all_res = self.b1500.list_all_resources()
        gpib = [r for r in all_res if "GPIB" in r.upper()]
        usb  = [r for r in all_res if "USB"  in r.upper()]
        self.combo_b1500.addItems(gpib)
        self.combo_power_meter.addItems(usb)
        self.log(f"Found {len(gpib)} GPIB and {len(usb)} USB resources")

    def connect_b1500(self) -> None:
        if self.b1500.connected:
            self.b1500.disconnect()
            self.label_b1500_status.setText("Not connected")
            self.label_b1500_status.setStyleSheet("color: red;")
            self.btn_connect_b1500.setText("Connect")
            self.log("B1500 disconnected")
        else:
            resource = self.combo_b1500.currentText()
            if not resource:
                QMessageBox.warning(self, "Error", "No B1500 resource selected")
                return
            ok, msg = self.b1500.connect(resource)
            if ok:
                self.label_b1500_status.setText(f"Connected: {self.b1500.idn[:50]}...")
                self.label_b1500_status.setStyleSheet("color: green;")
                self.btn_connect_b1500.setText("Disconnect")
                self.log(f"B1500 connected: {self.b1500.idn}")
                _, module_info = self.b1500.quick_module_check()
                self.log(f"Module check: {module_info}")
            else:
                QMessageBox.warning(self, "Connection Failed", msg)
                self.log(f"B1500 failed: {msg}")

    def connect_power_meter(self) -> None:
        if self.power_meter.connected:
            self.power_meter.disconnect()
            self.label_pm_status.setText("Not connected")
            self.label_pm_status.setStyleSheet("color: red;")
            self.btn_connect_pm.setText("Connect")
            self.log("Power meter disconnected")
        else:
            resource = self.combo_power_meter.currentText()
            if not resource:
                QMessageBox.warning(self, "Error", "No power meter resource selected")
                return
            ok, msg = self.power_meter.connect(resource)
            if ok:
                self.label_pm_status.setText(f"Connected: {self.power_meter.idn[:50]}...")
                self.label_pm_status.setStyleSheet("color: green;")
                self.btn_connect_pm.setText("Disconnect")
                self.log(f"Power meter connected: {self.power_meter.idn}")
            else:
                QMessageBox.warning(self, "Connection Failed", msg)
                self.log(f"Power meter failed: {msg}")

    # ==================================================================
    # Configuration helpers
    # ==================================================================

    def _on_integration_mode_changed(self, index: int) -> None:
        mode = self.combo_integration_mode.currentText()
        if mode == "Auto":
            self.combo_adc_type.setEnabled(True)
            self.spin_integration_n.setEnabled(True)
            self.spin_aperture.setEnabled(False)
        elif mode == "PLC":
            self.combo_adc_type.setEnabled(False)
            self.spin_integration_n.setEnabled(True)
            self.spin_aperture.setEnabled(False)
        else:  # Manual
            self.combo_adc_type.setEnabled(True)
            self.spin_integration_n.setEnabled(False)
            self.spin_aperture.setEnabled(True)

    def _get_integration_string(self) -> str:
        mode = self.combo_integration_mode.currentText()
        if mode == "Auto":
            adc = "SHORT" if self.combo_adc_type.currentIndex() == 0 else "LONG"
            return f"AUTO_{adc}_{self.spin_integration_n.value()}"
        if mode == "PLC":
            return f"PLC_{self.spin_integration_n.value()}"
        return f"MANUAL_{self.spin_aperture.value()}"

    def _browse_folder(self) -> None:
        current = self.edit_folder_path.text()
        if not Path(current).exists():
            current = str(Path(__file__).parents[2])
        folder = QFileDialog.getExistingDirectory(self, "Select Output Folder", current)
        if folder:
            self.edit_folder_path.setText(folder)

    def get_config(self) -> SweepConfig:
        """Assemble a :class:`SweepConfig` from the current GUI widget values."""
        mode = "iv" if self.combo_mode.currentIndex() == 0 else "vi"
        return SweepConfig(
            smu               = self.spin_smu.value(),
            mode              = mode,
            start             = self.spin_start.value(),
            stop              = self.spin_stop.value(),
            steps             = self.spin_steps.value(),
            dwell_s           = self.spin_dwell.value(),
            two_direction     = self.check_two_dir.isChecked(),
            compliance        = self.spin_compliance.value(),
            integration_time  = self._get_integration_string(),
            enable_power_meter= self.check_enable_pm.isChecked(),
            power_wavelength_nm = self.spin_wavelength.value(),
            power_range       = self.combo_pm_range.currentText(),
            power_averages    = self.spin_pm_avg.value(),
            enable_rollover   = self.check_enable_rollover.isChecked(),
            rollover_window   = self.spin_rollover_window.value(),
            rollover_threshold= self.spin_rollover_threshold.value() / 100.0,
            rollover_method   = self.combo_rollover_method.currentData(),
            rollover_alpha    = self.spin_rollover_alpha.value(),
            cusum_slack       = self.spin_cusum_slack.value() / 100.0,
            cusum_h           = self.spin_cusum_h.value(),
            output_folder     = self.edit_folder_path.text(),
            device_name       = self.edit_device_name.text(),
            autosave          = self.check_autosave.isChecked(),
            stop_on_compliance= self.check_stop_compliance.isChecked(),
        )

    # ==================================================================
    # Measurement control
    # ==================================================================

    def start_measurement(self) -> None:
        if not self.b1500.connected and not self.power_meter.connected:
            QMessageBox.warning(self, "Error", "No devices connected")
            return

        # Reset result labels
        for lbl in (self.label_peak_power, self.label_peak_voltage,
                    self.label_peak_current, self.label_stop_reason):
            lbl.setText("\u2014")

        # Clear plot buffers
        self.plot_voltages.clear()
        self.plot_currents.clear()
        self.plot_powers.clear()
        self._peak_marker_li = None
        self._peak_marker_pv = None
        self._update_plots()

        config = self.get_config()
        engine = SynchronizedMeasurementEngine(self.b1500, self.power_meter, config)

        self.worker = MeasurementWorker(engine)
        self.worker.point_complete.connect(self._on_point_complete)
        self.worker.progress.connect(self._on_progress)
        self.worker.log_message.connect(self.log)
        self.worker.rollover_detected.connect(self._on_rollover_detected)
        self.worker.finished_signal.connect(self._on_measurement_complete)

        self.btn_start.setEnabled(False)
        self.btn_stop.setEnabled(True)
        self.progress_bar.setValue(0)
        self.worker.start()

    def stop_measurement(self) -> None:
        if self.worker and self.worker.engine:
            self.worker.engine.stop()
            self.log("Stop requested…")

    # ==================================================================
    # Worker callbacks (run in main thread via Qt signal dispatch)
    # ==================================================================

    def _on_point_complete(self, point: MeasurementPoint) -> None:
        self.plot_voltages.append(point.voltage)
        self.plot_currents.append(point.current)
        self.plot_powers.append(point.optical_power)
        self._update_plots()

    def _on_progress(self, current: int, total: int) -> None:
        self.progress_bar.setMaximum(total)
        self.progress_bar.setValue(current)
        self.status_bar.showMessage(f"Measuring: {current}/{total}")

    def _on_rollover_detected(self, result: RolloverResult) -> None:
        if result.peak_point_index >= 0:
            self.label_peak_power.setText(f"{result.peak_power:.4e} W")
            self.label_peak_voltage.setText(f"{result.peak_voltage:.4f} V")
            self.label_peak_current.setText(f"{result.peak_current:.4e} A")

        reason_map = {
            "rollover":       "Rollover detected",
            "compliance":     "Compliance reached",
            "user_stop":      "Stopped by user",
            "sweep_complete": "Sweep complete",
        }
        self.label_stop_reason.setText(
            reason_map.get(result.stop_reason, result.stop_reason)
        )

        if result.peak_point_index >= 0 and len(self.plot_currents) > result.peak_point_index:
            self._peak_marker_li = (result.peak_current, result.peak_power)
            self._peak_marker_pv = (result.peak_voltage, result.peak_power)
            self._update_plots(show_peak=True)

        if result.detected:
            self.status_bar.showMessage(
                f"Rollover — Peak: {result.peak_power:.4e} W "
                f"@ V={result.peak_voltage:.4f} V, I={result.peak_current:.4e} A"
            )

    def _on_measurement_complete(self) -> None:
        self.btn_start.setEnabled(True)
        self.btn_stop.setEnabled(False)
        if not self.status_bar.currentMessage().startswith("Rollover"):
            self.status_bar.showMessage("Measurement complete")
        self.log("Measurement finished")

    # ==================================================================
    # Plotting
    # ==================================================================

    def _update_plots(self, show_peak: bool = False) -> None:
        if not self.plot_voltages:
            return

        v = np.array(self.plot_voltages)
        i = np.array(self.plot_currents)
        p = np.array(self.plot_powers)

        # I-V linear
        self.ax_iv.clear()
        self.ax_iv.plot(v, i, "b.-", linewidth=1, markersize=4)
        self.ax_iv.set_xlabel("Voltage (V)")
        self.ax_iv.set_ylabel("Current (A)")
        self.ax_iv.set_title("I-V Characteristic")
        self.ax_iv.grid(True, alpha=0.3)

        # I-V log
        self.ax_iv_log.clear()
        i_abs = np.abs(i)
        i_abs[i_abs < 1e-15] = 1e-15
        self.ax_iv_log.semilogy(v, i_abs, "b.-", linewidth=1, markersize=4)
        self.ax_iv_log.set_xlabel("Voltage (V)")
        self.ax_iv_log.set_ylabel("|Current| (A)")
        self.ax_iv_log.set_title("I-V Characteristic (Log Scale)")
        self.ax_iv_log.grid(True, alpha=0.3)

        # L-I
        self.ax_li.clear()
        self.ax_li.plot(i, p, "r.-", linewidth=1, markersize=4)
        if show_peak and self._peak_marker_li:
            pi, pp = self._peak_marker_li
            self.ax_li.plot(pi, pp, "k*", markersize=14, zorder=5,
                            label=f"Peak {pp:.3e} W")
            self.ax_li.legend(fontsize=8)
        self.ax_li.set_xlabel("Current (A)")
        self.ax_li.set_ylabel("Optical Power (W)")
        self.ax_li.set_title("L-I Characteristic")
        self.ax_li.grid(True, alpha=0.3)

        # P-V
        self.ax_pv.clear()
        self.ax_pv.plot(v, p, "g.-", linewidth=1, markersize=4)
        if show_peak and self._peak_marker_pv:
            pv, pp = self._peak_marker_pv
            self.ax_pv.plot(pv, pp, "k*", markersize=14, zorder=5,
                            label=f"Peak {pp:.3e} W")
            self.ax_pv.legend(fontsize=8)
        self.ax_pv.set_xlabel("Voltage (V)")
        self.ax_pv.set_ylabel("Optical Power (W)")
        self.ax_pv.set_title("P-V Characteristic")
        self.ax_pv.grid(True, alpha=0.3)

        self.figure.tight_layout()
        self.canvas.draw()

    # ==================================================================
    # Cleanup
    # ==================================================================

    def closeEvent(self, event) -> None:
        if self.worker and self.worker.isRunning():
            self.stop_measurement()
            self.worker.wait(2000)
        self.b1500.disconnect()
        self.power_meter.disconnect()
        event.accept()
