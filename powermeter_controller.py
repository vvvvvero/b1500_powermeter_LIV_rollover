#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
powermeter_controller.py
========================
Thread-safe VISA driver for Thorlabs PM100D / PM400 and any SCPI-compatible
optical power meter.

Standalone usage
----------------
    from b1500_powermeter_rollover.powermeter_controller import ThorlabsPowerMeterController

    pm = ThorlabsPowerMeterController()
    ok, msg = pm.connect("USB0::0x1313::0x8078::INSTR")
    if ok:
        pm.configure(wavelength_nm=850.0)
        power_w, status = pm.measure_power()
        print(f"Power = {power_w:.4e} W  ({status})")
        pm.disconnect()

© Veronica Gao ZHan  –  May 2026
"""

from __future__ import annotations

import threading
import time
from typing import List, Optional, Tuple

try:
    import pyvisa
    PYVISA_AVAILABLE = True
except ImportError:
    PYVISA_AVAILABLE = False


class ThorlabsPowerMeterController:
    """Thread-safe VISA driver for Thorlabs PM100D/PM400 power meters.

    All VISA I/O is guarded by ``self.lock`` so the object can safely be
    shared between a measurement thread and a GUI thread.

    Typical workflow
    ----------------
    1. ``connect(resource)``         – open VISA session, query IDN
    2. ``configure(wavelength, ...)``– set wavelength, range, averages
    3. ``measure_power()``           – read one power value
    4. ``disconnect()``              – release VISA session
    """

    # ------------------------------------------------------------------
    # SCPI command constants
    # ------------------------------------------------------------------
    SCPI_IDN            = "*IDN?"
    SCPI_MEAS_POWER     = "MEAS:POW?"
    SCPI_CONF_POWER     = "CONF:POW"
    SCPI_SET_WAVELENGTH = "SENS:CORR:WAV {}"
    SCPI_GET_WAVELENGTH = "SENS:CORR:WAV?"
    SCPI_AUTO_RANGE_ON  = "SENS:POW:RANG:AUTO ON"
    SCPI_AUTO_RANGE_OFF = "SENS:POW:RANG:AUTO OFF"
    SCPI_SET_RANGE      = "SENS:POW:RANG {}"
    SCPI_GET_RANGE      = "SENS:POW:RANG?"
    SCPI_SET_AVERAGES   = "SENS:AVER:COUN {}"
    SCPI_GET_AVERAGES   = "SENS:AVER:COUN?"

    def __init__(self) -> None:
        self.rm:        Optional[object] = None
        self.inst:      Optional[object] = None
        self.resource:  Optional[str]    = None
        self.idn:       str              = ""
        self.lock:      threading.Lock   = threading.Lock()
        self.connected: bool             = False

    # ------------------------------------------------------------------
    # Resource enumeration
    # ------------------------------------------------------------------

    def _resource_manager(self):
        """Return a pyvisa ResourceManager, falling back to the @py backend."""
        try:
            return pyvisa.ResourceManager()
        except Exception:
            return pyvisa.ResourceManager("@py")

    def list_resources(self, filter_pattern: str = "") -> List[str]:
        """Return VISA resource strings, optionally filtered by *filter_pattern*."""
        if not PYVISA_AVAILABLE:
            return []
        rm = self._resource_manager()
        try:
            all_res = rm.list_resources()
            if filter_pattern:
                return sorted(r for r in all_res if filter_pattern.upper() in r.upper())
            return sorted(all_res)
        except Exception as e:
            print(f"PowerMeter: error listing resources: {e}")
            return []
        finally:
            try:
                rm.close()
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Connection management
    # ------------------------------------------------------------------

    def connect(self, resource: str, timeout_ms: int = 5000) -> Tuple[bool, str]:
        """Open a VISA session to the power meter.

        Parameters
        ----------
        resource   : VISA resource string, e.g. ``"USB0::0x1313::0x8078::INSTR"``
        timeout_ms : VISA read timeout in milliseconds

        Returns
        -------
        (success, message)
        """
        self.disconnect()
        try:
            self.rm   = self._resource_manager()
            self.inst = self.rm.open_resource(resource)
            self.inst.timeout           = timeout_ms
            self.inst.write_termination = "\n"
            self.inst.read_termination  = "\n"
            with self.lock:
                self.idn = self.inst.query(self.SCPI_IDN).strip()
                self.inst.write(self.SCPI_CONF_POWER)
                time.sleep(0.1)
            self.resource  = resource
            self.connected = True
            return True, f"Connected: {self.idn}"
        except Exception as exc:
            self.disconnect()
            return False, f"Connection failed: {exc}"

    def disconnect(self) -> None:
        """Close the VISA session and reset all state."""
        if self.inst is not None:
            try:
                self.inst.close()
            except Exception:
                pass
        if self.rm is not None:
            try:
                self.rm.close()
            except Exception:
                pass
        self.inst      = None
        self.rm        = None
        self.resource  = None
        self.idn       = ""
        self.connected = False

    # ------------------------------------------------------------------
    # Configuration helpers
    # ------------------------------------------------------------------

    def set_wavelength(self, wavelength_nm: float) -> bool:
        """Set the responsivity-correction wavelength (nm)."""
        if not self.inst:
            return False
        try:
            with self.lock:
                self.inst.write(self.SCPI_SET_WAVELENGTH.format(wavelength_nm))
                time.sleep(0.05)
            return True
        except Exception as e:
            print(f"PowerMeter: set_wavelength error: {e}")
            return False

    def set_auto_range(self, enabled: bool = True) -> bool:
        """Enable or disable hardware auto-ranging."""
        if not self.inst:
            return False
        try:
            with self.lock:
                cmd = self.SCPI_AUTO_RANGE_ON if enabled else self.SCPI_AUTO_RANGE_OFF
                self.inst.write(cmd)
                time.sleep(0.05)
            return True
        except Exception as e:
            print(f"PowerMeter: set_auto_range error: {e}")
            return False

    def set_averages(self, count: int) -> bool:
        """Set the number of hardware averages per reading."""
        if not self.inst:
            return False
        try:
            with self.lock:
                self.inst.write(self.SCPI_SET_AVERAGES.format(count))
                time.sleep(0.05)
            return True
        except Exception as e:
            print(f"PowerMeter: set_averages error: {e}")
            return False

    def configure(
        self,
        wavelength_nm: float = 850.0,
        auto_range: bool = True,
        averages: int = 1,
    ) -> bool:
        """One-call convenience wrapper that sets wavelength, range, and averages.

        Returns True only if all three sub-commands succeeded.
        """
        ok = True
        ok &= self.set_wavelength(wavelength_nm)
        ok &= self.set_auto_range(auto_range)
        ok &= self.set_averages(averages)
        return ok

    # ------------------------------------------------------------------
    # Measurement
    # ------------------------------------------------------------------

    def measure_power(self) -> Tuple[float, str]:
        """Trigger and read one optical-power sample.

        Returns
        -------
        (power_watts, status_string)
            *power_watts* is 0.0 on error.
            *status_string* is ``"OK"`` on success or an error description.
        """
        if not self.inst:
            return 0.0, "Not connected"
        try:
            with self.lock:
                resp = self.inst.query(self.SCPI_MEAS_POWER).strip()
            return float(resp), "OK"
        except ValueError:
            return 0.0, f"Parse error: {resp!r}"
        except Exception as e:
            return 0.0, f"Measurement error: {e}"
