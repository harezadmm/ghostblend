"""Ghostblend worker entry point.

Executed by `blender -b --factory-startup --python run.py -- --session <dir> ...`.
"""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from gb import main as _main  # noqa: E402

_main.main(sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else [])
