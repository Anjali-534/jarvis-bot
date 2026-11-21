"""Stop Jarvis from starting when you log in to Windows."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from jarvis import startup  # noqa: E402

if __name__ == "__main__":
    if startup.uninstall():
        print(f"Removed {startup.shortcut_path()}. Jarvis won't start with Windows any more.")
    else:
        print("Jarvis wasn't set to start with Windows; nothing to remove.")
