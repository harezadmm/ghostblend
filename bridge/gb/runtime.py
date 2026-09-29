"""Execution state shared by the main loop, the stdin reader thread, and run_python."""
import contextlib
import ctypes
import threading


class GhostInterrupt(BaseException):
    """Raised asynchronously in the main thread to stop long-running agent code.

    It derives from BaseException so a broad `except Exception:` in agent code
    cannot swallow it.
    """


class _Exec:
    lock = threading.Lock()
    current_id = None
    interruptible = False
    main_ident = None


def set_main_thread():
    _Exec.main_ident = threading.get_ident()


def begin(request_id):
    with _Exec.lock:
        _Exec.current_id = request_id
        _Exec.interruptible = False


def end():
    with _Exec.lock:
        _Exec.current_id = None
        _Exec.interruptible = False


@contextlib.contextmanager
def interruptible():
    """Mark the enclosed block (agent code) as safe to interrupt."""
    with _Exec.lock:
        _Exec.interruptible = True
    try:
        yield
    finally:
        with _Exec.lock:
            _Exec.interruptible = False


def request_interrupt(request_id):
    """Called from the reader thread. Returns True if an interrupt was injected."""
    with _Exec.lock:
        if not (_Exec.interruptible and _Exec.current_id == request_id and _Exec.main_ident):
            return False
        fn = ctypes.pythonapi.PyThreadState_SetAsyncExc
        fn.argtypes = [ctypes.c_ulong, ctypes.py_object]
        fn.restype = ctypes.c_int
        return fn(_Exec.main_ident, GhostInterrupt) == 1
