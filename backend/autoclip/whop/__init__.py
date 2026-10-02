"""Whop module within autoclip package namespace."""

import sys
from pathlib import Path

# Ensure root whop module is accessible
root_dir = Path(__file__).resolve().parent.parent.parent.parent
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))

from whop import *
