#!/usr/bin/env python3
"""R4 stock-Library tile A/B driver (defaults to tile; --entry uri is explicit)."""
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parent))
from analyze import main

if __name__=='__main__':sys.exit(main('tile'))
