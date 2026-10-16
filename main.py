#!/usr/bin/env python
"""
LoomStroke main entry point.

This script starts the LoomStroke application with crash logging enabled.
"""

from __future__ import annotations

import importlib
import os
import sys
import tempfile
from pathlib import Path

# Add src to path
src_path = Path(__file__).parent / "src"
if src_path.exists():
    sys.path.insert(0, str(src_path))


def show_crash_log_info():
    """Display crash log location at startup."""
    crash_log_path = os.path.join(tempfile.gettempdir(), "loomstroke_crash.log")
    print("=" * 60)
    print("LoomStroke v0.1.2 - Starting...")
    print(f"Crash log location: {crash_log_path}")
    print("If the application crashes, check this file for details")
    print("=" * 60)


def _apply_startup_flags(argv: list[str]) -> None:
    """Apply lightweight diagnostic flags before importing the Qt app."""
    cpu_flags = {"--cpu-canvas", "--force-cpu-canvas"}
    perf_flags = {"--diagnose-pen-lag", "--pen-lag-diag"}
    kept = [argv[0]]
    for arg in argv[1:]:
        if arg in cpu_flags:
            os.environ["LOOMSTROKE_FORCE_CPU_CANVAS"] = "1"
            continue
        if arg in perf_flags:
            os.environ["LOOMSTROKE_PEN_LAG_DIAG"] = "1"
            continue
        kept.append(arg)
    argv[:] = kept


def main() -> int:
    """Main application entry point."""
    _apply_startup_flags(sys.argv)
    try:
        app_main = importlib.import_module("loomstroke.app").main
    except ImportError as e:
        print(f"Error: required module could not be imported: {e}")
        print("\nInstall dependencies with:")
        print("  pip install -e .")
        return 1

    show_crash_log_info()
    return app_main()


if __name__ == "__main__":
    sys.exit(main())
