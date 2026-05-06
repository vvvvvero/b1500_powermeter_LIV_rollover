# b1500_powermeter_rollover

[![PyPI](https://img.shields.io/pypi/v/b1500-powermeter-rollover)](https://pypi.org/project/b1500-powermeter-rollover/)
[![Python](https://img.shields.io/pypi/pyversions/b1500-powermeter-rollover)](https://pypi.org/project/b1500-powermeter-rollover/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

**Synchronized Keysight B1500 + Thorlabs power-meter IV sweep with four-algorithm rollover detection.**

© Veronica Gao Zhan – May 2026

---

## Features

| Feature | Detail |
|---|---|
| IV sweep | Point-by-point sourcing via B1500 SMU (IV or VI mode) |
| Optical power | Real-time Thorlabs PM100D / PM400 readout per point |
| Rollover detection | **4 algorithms**: CUSUM (default), EWMA, Rolling Average, Regression (sklearn) |
| GUI | PyQt5 scrollable control panel + live 2×2 matplotlib canvas |
| CLI | Full headless operation with `argparse` |
| CSV export | Timestamped file per sweep with rollover summary header |

---

## Package Structure

```
b1500_powermeter_rollover/
├── __init__.py            ← public API re-exports
├── __main__.py            ← python -m b1500_powermeter_rollover
├── config.py              ← SweepConfig, MeasurementPoint, RolloverResult
├── b1500_controller.py    ← thread-safe VISA driver for Keysight B1500
├── powermeter_controller.py ← thread-safe VISA driver for Thorlabs PM100D/PM400
├── rollover_detector.py   ← instrument-agnostic online rollover detector
├── engine.py              ← SynchronizedMeasurementEngine (no GUI dependency)
├── cli.py                 ← build_parser() + run_cli()
├── gui/
│   ├── __init__.py
│   ├── worker.py          ← MeasurementWorker (QThread)
│   └── main_window.py     ← SynchronizedMeasurementGUI (QMainWindow)
├── requirements.txt
└── pyproject.toml
```

---

## Installation

```bash
# From the B1500/ folder (editable install)
pip install -e ".[all]"

# Or install only the core (no GUI, no ML)
pip install -e .

# Or manually
pip install pyvisa pyvisa-py pyusb numpy PyQt5 matplotlib scikit-learn
```

---

## Quick Start

### GUI (interactive)

```bash
python -m b1500_powermeter_rollover
```

### CLI (headless / automated)

```bash
# List connected instruments
python -m b1500_powermeter_rollover --list

# Run an IV sweep with CUSUM rollover detection
python -m b1500_powermeter_rollover \
    --b1500 GPIB0::17::INSTR \
    --pm    USB0::0x1313::0x8078::INSTR \
    --start 0 --stop 2.5 --steps 26 \
    --compliance 0.5 --dwell 0.1 \
    --rollover --method cusum --threshold 90 \
    --output ./results --name MyLaser
```

### Python API

```python
from b1500_powermeter_rollover import (
    SweepConfig,
    B1500Controller,
    ThorlabsPowerMeterController,
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

r = eng.rollover_result
print(f"Peak power {r.peak_power:.4e} W at {r.peak_voltage:.4f} V")
```

### Standalone rollover detector (no instruments)

```python
from b1500_powermeter_rollover import SweepConfig, RolloverDetector

cfg = SweepConfig(rollover_method="cusum", rollover_threshold=0.90)
det = RolloverDetector(cfg)

peak = 0.0
for power in my_power_readings:
    peak = max(peak, power)
    triggered, info = det.update(power, peak)
    if triggered:
        print("Rollover detected!", info)
        break
```

---

## Detection Algorithms

| Method | Latency | Notes |
|---|---|---|
| `cusum` *(default)* | 1–3 pts | Lower-sided CUSUM (Page 1954). Scale-invariant. O(1)/sample |
| `ewma` | ~window/2 | Exponential moving average. Tunable with `--alpha` |
| `rolling_avg` | window pts | Classic windowed mean. Robust to impulse noise |
| `regression` | window pts | sklearn LinearRegression (batch) + SGDRegressor (online) |

---

## Acknowledgements

This project was developed using **vibe coding** — an AI-assisted development workflow powered by [GitHub Copilot](https://github.com/features/copilot). The architecture, code structure, and implementation were generated through iterative natural-language prompting and human review.

## License

© Veronica Gao ZHan
