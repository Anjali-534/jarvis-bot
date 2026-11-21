"""Start Jarvis in the background: tray icon, no console window.

Double-click this file, or run:  pythonw run_jarvis.pyw
Output goes to data/logs/jarvis.log (tray menu > Open logs).
"""
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent
os.chdir(REPO)
sys.path.insert(0, str(REPO))

from jarvis.tray import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
