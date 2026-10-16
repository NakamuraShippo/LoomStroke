"""
LoomStroke package entry point.
Enables execution via: python -m loomstroke

Per Master.md specifications, this is the single entry point for the application.
"""

from __future__ import annotations

import os
import sys

# パッケージルートをPythonパスに追加(直接実行対応)
if __package__ is None and not hasattr(sys, "frozen"):
    # 直接実行された場合の対処
    file_dir = os.path.dirname(os.path.abspath(__file__))
    parent_dir = os.path.dirname(os.path.dirname(file_dir))
    sys.path.insert(0, parent_dir)

    # 直 import を使用
    from loomstroke.app import main
else:
    # 通常のパッケージ実行
    from .app import main


if __name__ == "__main__":
    sys.exit(main())
