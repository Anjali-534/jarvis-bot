"""Single instance: a named mutex on Windows (lock file elsewhere), plus a "show yourself" signal.

The first Jarvis holds the lock and waits on a named event. A second launch finds the
lock taken, sets the event (so the running Jarvis wakes up and shows a notification)
and exits.
"""
import logging
import os
import sys
import threading

import config

log = logging.getLogger(__name__)

DEFAULT_NAME = "JarvisVoiceAssistant"
ERROR_ALREADY_EXISTS = 183
EVENT_MODIFY_STATE = 0x0002
WAIT_OBJECT_0 = 0


def _kernel32():
    import ctypes
    from ctypes import wintypes

    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.CreateMutexW.restype = wintypes.HANDLE
    k.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
    k.CreateEventW.restype = wintypes.HANDLE
    k.CreateEventW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR]
    k.OpenEventW.restype = wintypes.HANDLE
    k.OpenEventW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
    k.SetEvent.argtypes = [wintypes.HANDLE]
    k.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    k.WaitForSingleObject.restype = wintypes.DWORD
    k.CloseHandle.argtypes = [wintypes.HANDLE]
    return k, ctypes


class SingleInstance:
    def __init__(self, name=DEFAULT_NAME, lock_dir=None, use_mutex=None):
        self.name = name
        self.use_mutex = (sys.platform == "win32") if use_mutex is None else use_mutex
        self.lock_path = (lock_dir or config.LOG_DIR.parent) / f"{name}.lock"
        self._mutex = None
        self._lock_file = None
        self._event = None
        self._stop = threading.Event()
        self.acquired = False

    # ------------------------------------------------------------ lock
    def acquire(self):
        """True if this process is now the only Jarvis."""
        self.acquired = self._acquire_mutex() if self.use_mutex else self._acquire_file()
        return self.acquired

    def _acquire_mutex(self):
        k, ctypes = _kernel32()
        handle = k.CreateMutexW(None, False, f"Local\\{self.name}")
        if not handle:
            raise OSError(ctypes.get_last_error(), "CreateMutexW failed")
        if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
            k.CloseHandle(handle)
            return False
        self._mutex = handle
        return True

    def _acquire_file(self):
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        fh = open(self.lock_path, "a+")
        try:
            if sys.platform == "win32":
                import msvcrt

                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            fh.close()
            return False
        fh.seek(0)
        fh.truncate()
        fh.write(str(os.getpid()))
        fh.flush()
        self._lock_file = fh
        return True

    def release(self):
        self._stop.set()
        if self._mutex:
            k, _ = _kernel32()
            k.CloseHandle(self._mutex)
            self._mutex = None
        if self._lock_file:
            try:
                if sys.platform == "win32":
                    import msvcrt

                    self._lock_file.seek(0)
                    msvcrt.locking(self._lock_file.fileno(), msvcrt.LK_UNLCK, 1)
            except OSError:
                pass
            self._lock_file.close()
            self._lock_file = None
        if self._event:
            k, _ = _kernel32()
            k.CloseHandle(self._event)
            self._event = None
        self.acquired = False

    # ------------------------------------------------------------ "show yourself" signal (Windows)
    @property
    def event_name(self):
        return f"Local\\{self.name}-show"

    def signal_existing(self):
        """Ask the running Jarvis to wake up and show itself. True if it was reached."""
        if sys.platform != "win32":
            return False
        k, _ = _kernel32()
        handle = k.OpenEventW(EVENT_MODIFY_STATE, False, self.event_name)
        if not handle:
            return False
        try:
            return bool(k.SetEvent(handle))
        finally:
            k.CloseHandle(handle)

    def listen(self, callback):
        """Call callback() whenever another launch signals us. Runs on a daemon thread."""
        if sys.platform != "win32":
            return None
        k, _ = _kernel32()
        self._event = k.CreateEventW(None, False, False, self.event_name)  # auto-reset

        def wait_loop():
            while not self._stop.is_set():
                if k.WaitForSingleObject(self._event, 500) == WAIT_OBJECT_0 and not self._stop.is_set():
                    try:
                        callback()
                    except Exception:
                        log.exception("show callback failed")

        thread = threading.Thread(target=wait_loop, name="instance-signal", daemon=True)
        thread.start()
        return thread

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.release()
