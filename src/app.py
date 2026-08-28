"""Vercel-recognized FastAPI entry point for the procurement workflow API."""

import sys
from pathlib import Path

# Vercel imports this file by path and does not automatically add ``src`` to
# sys.path, unlike the local package-aware uv/FastAPI command.
source_root = str(Path(__file__).resolve().parent)
if source_root not in sys.path:
    sys.path.insert(0, source_root)

from procurement_demo.api import app

__all__ = ["app"]
