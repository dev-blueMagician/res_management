"""Application entry point: ``python -m restaurant``. The app itself is in bridge/app.py."""

from __future__ import annotations

import sys

from restaurant.bridge.app import main

if __name__ == "__main__":
    sys.exit(main())
