#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
b1500_controller.py
===================
Low-level VISA driver for the Keysight B1500 Semiconductor Parameter Analyzer.

Standalone usage
----------------
    from b1500_powermeter_rollover.b1500_controller import B1500Controller
    from b1500_powermeter_rollover.config import SweepConfig

    b = B1500Controller()
    ok, msg = b.connect("GPIB0::17::INSTR")
    if ok:
        cfg = SweepConfig(mode="iv", start=0, stop=2, steps=21)
        b.configure_sweep(cfg)
        v, i = b.set_bias_and_measure(1, 1.0, cfg)
        print(f"V={v:.4f} V  I={i:.4e} A")
        b.output_off(1)
        b.disconnect()

© Veronica Gao ZHan  –  May 2026
"""

from __future__ import annotations

import threading
import time
from typing import List, Optional, Tuple

try:
    import pyvisa
    from pyvisa.errors import VisaIOError
    PYVISA_AVAILABLE = True
except ImportError:
    PYVISA_AVAILABLE = False

from .config import SweepConfig

# Suppress Windows crash dialogs caused by USB/GPIB driver errors
try:
    import ctypes
    ctypes.windll.kernel32.SetErrorMode(0x0001 | 0x0002)
except (AttributeError, OSError, TypeError):
    pass


class B1500Controller:
    """Thread-safe VISA driver for the Keysight B1500A/B1505A/B1506A.

    All VISA I/O is guarded by ``self.lock`` (a :class:`threading.Lock`) so
    this object can be shared between a measurement thread and a GUI thread
    without data races.

    Typical workflow
    ----------------
    1. ``connect(resource)``          – open VISA session, query IDN
    2. ``configure_sweep(config)``    – set up SMU channel, ADC, format
    3. ``set_bias_and_measure(...)``  – set source, trigger, read result
    4. ``output_off(smu)``            – zero and close the SMU channel
    5. ``disconnect()``               – release VISA session
    """

    # ------------------------------------------------------------------
    # SCPI command constants
    # ------------------------------------------------------------------
    SCPI_IDN   = "*IDN?"
    SCPI_CLS   = "*CLS"
    SCPI_RST   = "*RST"
    SCPI_OPC   = "*OPC?"
    SCPI_FMT   = "FMT 1,0"      # ASCII, no header
    SCPI_ERR   = "ERR?"

    def __init__(self) -> None:
        self.rm:       Optional[object]  = None
        self.inst:     Optional[object]  = None
        self.resource: Optional[str]     = None
        self.idn:      str               = ""
        self.modules:  List[str]         = []
        self.lock:     threading.Lock    = threading.Lock()
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

    def list_gpib_resources(self) -> List[str]:
        """Return sorted list of GPIB VISA resource strings."""
        if not PYVISA_AVAILABLE:
            return []
        rm = self._resource_manager()
        try:
            return sorted(r for r in rm.list_resources() if r.upper().startswith("GPIB"))
        except Exception as e:
            print(f"B1500: error listing GPIB resources: {e}")
            return []
        finally:
            try:
                rm.close()
            except Exception:
                pass

    def list_all_resources(self) -> List[str]:
        """Return sorted list of all VISA resource strings."""
        if not PYVISA_AVAILABLE:
            return []
        rm = self._resource_manager()
        try:
            return sorted(rm.list_resources())
        except Exception as e:
            print(f"B1500: error listing resources: {e}")
            return []
        finally:
            try:
                rm.close()
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Connection management
    # ------------------------------------------------------------------

    def connect(self, resource: str, timeout_ms: int = 15000) -> Tuple[bool, str]:
        """Open a VISA session to the B1500.

        Parameters
        ----------
        resource   : VISA resource string, e.g. ``"GPIB0::17::INSTR"``
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
                self.inst.write(self.SCPI_FMT)
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
        self.modules   = []
        self.connected = False

    def verify_connection(self) -> Tuple[bool, str]:
        """Ping the instrument with ``*IDN?`` to confirm the session is live."""
        if not self.connected:
            return False, "Not connected (flag is False)"
        if not self.inst:
            self.connected = False
            return False, "No instrument handle"
        if not self.rm:
            self.connected = False
            return False, "No resource manager"
        try:
            with self.lock:
                response = self.inst.query(self.SCPI_IDN).strip()
                if response:
                    return True, f"Connection verified: {response[:50]}"
                self.connected = False
                return False, "Empty IDN response"
        except Exception as e:
            self.connected = False
            return False, f"Connection verification failed: {e}"

    # ------------------------------------------------------------------
    # Module discovery
    # ------------------------------------------------------------------

    def quick_module_check(self) -> Tuple[bool, str]:
        """Probe SMU channels 1–10 and report which ones respond."""
        if not self.inst:
            return False, "Not connected"
        available_smus: List[int] = []
        try:
            with self.lock:
                self.inst.timeout = 5000
                for smu in range(1, 11):
                    try:
                        self.inst.write(f"CN {smu}")
                        time.sleep(0.05)
                        err = self.inst.query(self.SCPI_ERR).strip()
                        if err.startswith("0"):
                            available_smus.append(smu)
                            self.inst.write(f"CL {smu}")
                    except Exception:
                        continue
                self.inst.timeout = 15000
            if available_smus:
                return True, f"Available SMU channels: {available_smus}"
            return True, "Could not detect SMU channels. Try SMU 1-4."
        except Exception as e:
            return False, f"Module check error: {e}"

    # ------------------------------------------------------------------
    # Low-level I/O helpers
    # ------------------------------------------------------------------

    def _safe_read(self) -> str:
        """Read raw bytes and decode with fallback encodings."""
        if not self.inst:
            return ""
        try:
            raw = self.inst.read_raw()
            for enc in ("ascii", "latin-1", "utf-8"):
                try:
                    return raw.decode(enc).strip()
                except UnicodeDecodeError:
                    continue
            return raw.decode("ascii", errors="ignore").strip()
        except Exception:
            return ""

    # ------------------------------------------------------------------
    # Sweep configuration
    # ------------------------------------------------------------------

    def set_integration_time(self, smu: int, integration: str) -> None:
        """Set the ADC integration time / mode for one SMU channel.

        Parameters
        ----------
        smu         : SMU channel number
        integration : integration descriptor – see :attr:`SweepConfig.integration_time`
        """
        if not self.inst:
            raise RuntimeError("Not connected")
        with self.lock:
            iu = integration.upper()
            if iu.startswith("AUTO_"):
                parts = integration.split("_")
                mode_type = parts[1].upper() if len(parts) > 1 else "SHORT"
                num = int(parts[2]) if len(parts) > 2 else 1
                adc_code = 0 if mode_type == "SHORT" else (1 if mode_type == "LONG" else 0)
                self.inst.write(f"AAD {smu},{adc_code}")
                self.inst.write(f"AIT 0,0,{num}")
            elif iu.startswith("PLC_"):
                plc = int(integration.split("_")[1]) if "_" in integration else 1
                self.inst.write(f"AIT 0,2,{plc}")
            elif iu.startswith("MANUAL_"):
                aperture = float(integration.split("_")[1]) if "_" in integration else 0.001
                self.inst.write(f"AIT 0,1,{aperture}")
            elif iu in ("SHORT", "MEDIUM", "LONG"):
                self.inst.write(f"AAD {smu},{iu}")
            else:
                try:
                    aperture = float(integration)
                    self.inst.write(f"AIT 0,1,{aperture}")
                except ValueError:
                    self.inst.write(f"AAD {smu},MEDIUM")

    def configure_sweep(self, cfg: SweepConfig) -> None:
        """Configure the B1500 for point-by-point measurement using *cfg*.

        Sets output format, connects the SMU channel, configures ADC type,
        averaging, measurement range, hold/step timings, and measurement mode.

        Raises
        ------
        RuntimeError if not connected.
        """
        smu = cfg.smu
        with self.lock:
            if not self.inst or not self.connected:
                raise RuntimeError("Not connected")
            try:
                # Drain error queue
                for _ in range(5):
                    try:
                        if self.inst.query(self.SCPI_ERR).strip().startswith("0"):
                            break
                    except Exception:
                        break
                time.sleep(0.1)

                self.inst.write(self.SCPI_FMT)
                time.sleep(0.05)
                self.inst.write(f"CN {smu}")
                time.sleep(0.1)
                self.inst.write(f"AAD {smu},1")     # high-resolution ADC
                time.sleep(0.05)
                self.inst.write("AV 1,0")            # 1 average, auto mode
                time.sleep(0.05)

                range_code = cfg.meas_range if cfg.meas_range is not None else 0
                if cfg.mode == "iv":
                    self.inst.write(f"RI {smu},{range_code}")
                else:
                    self.inst.write(f"RV {smu},{range_code}")
                time.sleep(0.05)

                step_delay = max(cfg.dwell_s, 0.0)
                self.inst.write(f"WT 0.0,{step_delay}")
                time.sleep(0.05)
                self.inst.write(f"MM 1,{smu}")       # spot measurement mode
            except Exception as e:
                print(f"B1500: configure_sweep error: {e}")
                raise

    # ------------------------------------------------------------------
    # Per-point measurement
    # ------------------------------------------------------------------

    def set_bias_and_measure(
        self,
        smu: int,
        set_value: float,
        cfg: SweepConfig,
    ) -> Tuple[float, float]:
        """Set source value, trigger, and return ``(voltage, current)``.

        In IV mode (*cfg.mode* == ``"iv"``): sources *set_value* V, returns
        ``(set_value, measured_current)``.
        In VI mode: sources *set_value* A, returns ``(measured_voltage, set_value)``.
        """
        with self.lock:
            if not self.inst or not self.connected:
                raise RuntimeError("Not connected")
            try:
                if cfg.mode == "iv":
                    self.inst.write(f"DV {smu},0,{set_value},{cfg.compliance}")
                else:
                    self.inst.write(f"DI {smu},0,{set_value},{cfg.compliance}")

                if cfg.dwell_s > 0:
                    time.sleep(cfg.dwell_s)

                self.inst.write("XE")

                old_timeout = self.inst.timeout
                self.inst.timeout = 5000
                try:
                    resp = self._safe_read()
                finally:
                    self.inst.timeout = old_timeout
            except Exception as e:
                print(f"B1500: measurement error at {set_value}: {e}")
                return (set_value, 0.0) if cfg.mode == "iv" else (0.0, set_value)

        # Parse outside the lock
        try:
            resp = resp.strip()
            if not resp:
                return (set_value, 0.0) if cfg.mode == "iv" else (0.0, set_value)

            values: List[float] = []
            for p in resp.replace(";", ",").split(","):
                p = p.strip()
                if not p:
                    continue
                try:
                    # Strip leading status characters (letters) before the number
                    num_start = next(
                        (i for i, c in enumerate(p) if c in "+-0123456789."),
                        None,
                    )
                    if num_start is not None:
                        values.append(float(p[num_start:]))
                except Exception:
                    pass

            if values:
                measured = values[0]
                return (set_value, measured) if cfg.mode == "iv" else (measured, set_value)
        except Exception as e:
            print(f"B1500: parse error: {e}  response: {resp!r}")

        return (set_value, 0.0) if cfg.mode == "iv" else (0.0, set_value)

    # ------------------------------------------------------------------
    # Output control
    # ------------------------------------------------------------------

    def output_off(self, smu: int) -> None:
        """Zero and close the SMU output safely."""
        if not self.inst:
            return
        with self.lock:
            try:
                self.inst.write(f"DV {smu},0,0,0.01")
                time.sleep(0.05)
                self.inst.write(f"CL {smu}")
            except Exception:
                pass
