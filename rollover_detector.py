#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
rollover_detector.py
====================
Online rollover detector with four interchangeable strategies.

The detector is **instrument-agnostic**: it takes raw power readings
(floats in any power unit) and returns a boolean decision plus
diagnostic information.  It can be used standalone, without any
instrument hardware:

    from b1500_powermeter_rollover.rollover_detector import RolloverDetector
    from b1500_powermeter_rollover.config import SweepConfig

    cfg = SweepConfig(rollover_method="cusum", rollover_threshold=0.90)
    det = RolloverDetector(cfg)

    peak = 0.0
    for power in my_power_stream:
        peak = max(peak, power)
        triggered, info = det.update(power, peak)
        if triggered:
            print("Rollover detected!", info)
            break

Strategies
----------
rolling_avg  (original, robust)
    Waits for *window_size* readings, then:
        mean(window) < threshold × peak  →  trigger.

ewma  (faster)
    Exponentially-Weighted Moving Average:
        ewma ← α · power + (1−α) · ewma
    Responds before the window is full.  α ≈ 0.3 balances speed
    and noise rejection.

cusum  (fastest – default)
    Lower-sided CUSUM (Page 1954):
        S_neg ← max(0, S_neg + (threshold·peak − power) − k·peak)
    Triggers when S_neg > H·peak.
    Both k (slack) and H (decision interval) are expressed relative to
    peak power so the detector is scale-invariant.  Typical latency:
    1–3 steps for a clean rollover.  O(1) per sample.

regression  (ML – sklearn / numpy fallback)
    Fits sklearn.LinearRegression to the last *window_size* readings
    (batch) and simultaneously calls SGDRegressor.partial_fit() for
    online incremental learning.  Falls back to numpy.polyfit when
    sklearn is not installed.
    Triggers when: slope < 0  AND  mean(window) < threshold × peak.

© Veronica Gao ZHan  –  May 2026
"""

from __future__ import annotations

from collections import deque
from typing import Any, Dict, Optional, Tuple

import numpy as np

try:
    from sklearn.linear_model import LinearRegression as _LR, SGDRegressor as _SGDRegressor
    SKLEARN_AVAILABLE = True
except ImportError:
    SKLEARN_AVAILABLE = False

from .config import SweepConfig


class RolloverDetector:
    """Online, stateful rollover detector.

    Re-use across multiple sweeps by calling :meth:`reset` between sweeps.

    Parameters
    ----------
    config : SweepConfig
        The detector reads ``rollover_method``, ``rollover_window``,
        ``rollover_threshold``, ``rollover_alpha``, ``cusum_slack``,
        and ``cusum_h`` from *config*.
    """

    def __init__(self, config: SweepConfig) -> None:
        self.method:      str   = config.rollover_method
        self.window_size: int   = config.rollover_window
        self.threshold:   float = config.rollover_threshold
        self.alpha:       float = config.rollover_alpha
        self.cusum_slack: float = config.cusum_slack
        self.cusum_h:     float = config.cusum_h

        # Shared rolling window (rolling_avg, ewma, regression)
        self._window: deque = deque(maxlen=self.window_size)
        # EWMA state
        self._ewma:   Optional[float] = None
        # CUSUM lower-sided accumulator
        self._S_neg:  float = 0.0
        # Sample counter (warm-up guard)
        self._n:      int   = 0

        # Online ML model (regression method only)
        self._sgd: Optional[Any] = None
        if SKLEARN_AVAILABLE and self.method == "regression":
            self._sgd = self._make_sgd()

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def reset(self) -> None:
        """Reset all internal state.  Call before starting a new sweep."""
        self._window.clear()
        self._ewma  = None
        self._S_neg = 0.0
        self._n     = 0
        if SKLEARN_AVAILABLE and self.method == "regression":
            self._sgd = self._make_sgd()

    def update(
        self,
        power: float,
        peak_power: float,
    ) -> Tuple[bool, Dict[str, Any]]:
        """Feed one new power reading and return the stop decision.

        Parameters
        ----------
        power      : latest optical power reading (W or any consistent unit)
        peak_power : highest reading seen so far in this sweep

        Returns
        -------
        (triggered, info)
            *triggered* is True when the chosen algorithm decides that rollover
            has occurred.
            *info* is a dict of diagnostic values (method-dependent) useful for
            logging or plotting.
        """
        self._window.append(power)
        self._n += 1
        info: Dict[str, Any] = {"method": self.method, "n": self._n}

        # No meaningful peak yet – wait silently
        if peak_power <= 0.0:
            return False, info

        threshold_power = self.threshold * peak_power

        if self.method == "ewma":
            return self._check_ewma(power, threshold_power, info)
        if self.method == "cusum":
            return self._check_cusum(power, peak_power, threshold_power, info)
        if self.method == "regression":
            return self._check_regression(threshold_power, info)
        # Default / "rolling_avg"
        return self._check_rolling_avg(threshold_power, info)

    # ------------------------------------------------------------------
    # Strategy implementations
    # ------------------------------------------------------------------

    def _check_rolling_avg(
        self,
        threshold_power: float,
        info: Dict,
    ) -> Tuple[bool, Dict]:
        """Classic rolling-window mean check."""
        if len(self._window) < self.window_size:
            return False, info
        mean = sum(self._window) / len(self._window)
        info.update(window_mean=mean, threshold_power=threshold_power)
        return mean < threshold_power, info

    def _check_ewma(
        self,
        power: float,
        threshold_power: float,
        info: Dict,
    ) -> Tuple[bool, Dict]:
        """Exponentially-Weighted Moving Average check."""
        if self._ewma is None:
            self._ewma = power
            return False, info
        self._ewma = self.alpha * power + (1.0 - self.alpha) * self._ewma
        info.update(ewma=self._ewma, threshold_power=threshold_power)
        # Enforce warm-up
        if self._n < self.window_size:
            return False, info
        return self._ewma < threshold_power, info

    def _check_cusum(
        self,
        power: float,
        peak_power: float,
        threshold_power: float,
        info: Dict,
    ) -> Tuple[bool, Dict]:
        """Lower-sided CUSUM (Page 1954).

        S_neg accumulates evidence that the mean has shifted below
        *threshold_power*.  The slack *k* absorbs routine noise; *H* is
        the decision interval.  Both are relative to *peak_power* so the
        detector is scale-invariant.
        """
        if self._n < 2:
            return False, info

        k = self.cusum_slack * peak_power
        H = self.cusum_h    * peak_power

        deviation   = (threshold_power - power) - k
        self._S_neg = max(0.0, self._S_neg + deviation)

        info.update(S_neg=self._S_neg, H=H, threshold_power=threshold_power)
        return self._S_neg > H, info

    def _check_regression(
        self,
        threshold_power: float,
        info: Dict,
    ) -> Tuple[bool, Dict]:
        """ML linear regression on the last *window_size* readings.

        Batch fit (sklearn.LinearRegression or numpy.polyfit) provides the
        slope estimate.  An SGDRegressor.partial_fit() call additionally
        updates an online model with every new sample (true online ML).

        Triggers when: slope < 0  AND  window mean < threshold_power.
        """
        n = len(self._window)
        if n < self.window_size:
            return False, info

        y = np.array(list(self._window), dtype=float)
        x = np.arange(n, dtype=float).reshape(-1, 1)

        if SKLEARN_AVAILABLE:
            # Batch ML: LinearRegression (most reliable slope)
            model = _LR(fit_intercept=True)
            model.fit(x, y)
            slope = float(model.coef_[0])
            # Online ML: SGDRegressor incremental update
            if self._sgd is not None:
                try:
                    self._sgd.partial_fit(x[-1:], y[-1:])
                    info["sgd_slope"] = float(self._sgd.coef_[0])
                except Exception:
                    pass
        else:
            # Fallback: numpy batch linear regression
            coeffs = np.polyfit(x.ravel(), y, 1)
            slope  = float(coeffs[0])

        mean = float(y.mean())
        info.update(
            slope=slope,
            regression_mean=mean,
            threshold_power=threshold_power,
            sklearn=SKLEARN_AVAILABLE,
        )
        return (slope < 0.0) and (mean < threshold_power), info

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _make_sgd() -> Any:
        """Create and warm-start a SGDRegressor so coef_ is always defined."""
        sgd = _SGDRegressor(
            loss="squared_error",
            learning_rate="constant",
            eta0=0.001,
            max_iter=1,
            tol=None,
            warm_start=True,
            fit_intercept=True,
            random_state=42,
        )
        sgd.fit([[0.0]], [0.0])
        return sgd
