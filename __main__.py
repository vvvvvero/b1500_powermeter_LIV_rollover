#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
__main__.py
===========
Entry point for:

    python -m b1500_powermeter_rollover [args...]

If no arguments are passed (or if ``--gui`` is given), the PyQt5 GUI is
launched.  Any other argument triggers headless CLI mode.

© Veronica Gao ZHan  –  May 2026
"""

import sys

from .cli import build_parser, run_cli


def main() -> None:
    parser = build_parser()

    # No arguments → launch GUI
    if len(sys.argv) == 1 or "--gui" in sys.argv:
        _launch_gui()
        return

    args = parser.parse_args()
    sys.exit(run_cli(args))


def _launch_gui() -> None:
    """Start the PyQt5 GUI.  Exits via QApplication.exec_()."""
    try:
        from PyQt5.QtWidgets import QApplication
        app = QApplication(sys.argv)
        app.setStyle("Fusion")

        # Deferred import so non-GUI installs work fine in headless mode
        from .gui.main_window import SynchronizedMeasurementGUI
        win = SynchronizedMeasurementGUI()
        win.show()
        sys.exit(app.exec_())
    except ImportError as e:
        print(
            f"GUI unavailable ({e}).\n"
            "Install PyQt5:  pip install PyQt5\n"
            "Or run in CLI mode:  python -m b1500_powermeter_rollover --help",
            file=sys.stderr,
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
