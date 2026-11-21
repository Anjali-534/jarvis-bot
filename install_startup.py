"""Make Jarvis start (in the tray, without a console) every time you log in to Windows."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from jarvis import startup  # noqa: E402

if __name__ == "__main__":
    try:
        path = startup.install()
    except RuntimeError as exc:
        sys.exit(str(exc))
    spec = startup.shortcut_spec()
    print(f"Created {path}")
    print(f"  runs: {spec['target']} {spec['arguments']}")
    print(f"  in:   {spec['working_dir']}")
    print("Jarvis will start in the tray next time you log in. Undo with: python uninstall_startup.py")
