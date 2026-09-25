"""Unit tests of the qa-audit engine (docs/qa-audit.md §15). No browser needed.

Run with the rest of the framework: python -m unittest discover -s tests
"""

import os
import sys

SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)
