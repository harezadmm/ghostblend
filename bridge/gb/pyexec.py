"""run_python: execute agent-written bpy code in the live scene."""
import contextlib
import io
import linecache
import math
import traceback

import bmesh
import bpy
import mathutils
from mathutils import Euler, Matrix, Quaternion, Vector

from . import registry, runtime, util

OUTPUT_LIMIT = 20000
RESULT_LIMIT = 100000
FILENAME = "<run_python>"


class CappedIO(io.TextIOBase):
    """A text sink that keeps at most `limit` characters and counts the rest."""

    def __init__(self, limit):
        super().__init__()
        self.limit = limit
        self.parts = []
        self.size = 0
        self.dropped = 0

    def writable(self):
        return True

    def write(self, s):
        if not isinstance(s, str):
            s = str(s)
        room = self.limit - self.size
        if room > 0:
            chunk = s[:room]
            self.parts.append(chunk)
            self.size += len(chunk)
            self.dropped += len(s) - len(chunk)
        else:
            self.dropped += len(s)
        return len(s)

    def getvalue(self):
        text = "".join(self.parts)
        if self.dropped:
            text += f"\n[... {self.dropped} more characters truncated]"
        return text


class ExecError(Exception):
    """Failure inside agent code; `payload` is the error object sent back to the agent."""

    def __init__(self, payload):
        super().__init__(payload.get("message"))
        self.payload = payload


def _user_traceback(exc):
    frames = traceback.extract_tb(exc.__traceback__)
    start = next((i for i, f in enumerate(frames) if f.filename == FILENAME), None)
    if start is not None:
        frames = frames[start:]
    lines = traceback.format_list(frames)
    lines += traceback.format_exception_only(type(exc), exc)
    return ("Traceback (most recent call last):\n" + "".join(lines))[-4000:]


def _jsonable_result(value):
    import json

    data = util.to_jsonable(value)
    text = json.dumps(data, ensure_ascii=False)
    if len(text) > RESULT_LIMIT:
        return {"truncated_json": text[:RESULT_LIMIT], "note": f"result was {len(text)} characters; showing the first {RESULT_LIMIT}"}
    return data


@registry.command("run_python")
def run_python(args):
    code = args.get("code") or ""
    ns = {
        "__name__": "__ghostblend__",
        "bpy": bpy, "bmesh": bmesh, "mathutils": mathutils, "math": math,
        "Vector": Vector, "Matrix": Matrix, "Euler": Euler, "Quaternion": Quaternion,
        "result": None,
    }
    try:
        compiled = compile(code, FILENAME, "exec")
    except SyntaxError as e:
        raise ExecError({"type": "SyntaxError", "message": f"{e.msg} (line {e.lineno})",
                         "traceback": "".join(traceback.format_exception_only(type(e), e))})
    linecache.cache[FILENAME] = (len(code), None, code.splitlines(True), FILENAME)
    out, err = CappedIO(OUTPUT_LIMIT), CappedIO(OUTPUT_LIMIT)
    failure = None
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            with runtime.interruptible():
                exec(compiled, ns)
        except runtime.GhostInterrupt:
            failure = {"type": "Interrupted",
                       "message": "run_python was stopped: it exceeded timeout_s or the client cancelled the call"}
        except SystemExit as e:
            failure = {"type": "SystemExit",
                       "message": f"The code called sys.exit({e.code!r}); the Blender worker keeps running"}
        except BaseException as e:  # noqa: BLE001 - report every failure to the agent
            failure = {"type": type(e).__name__, "message": str(e) or repr(e), "traceback": _user_traceback(e)}
    if failure is not None:
        failure["stdout"] = out.getvalue()
        failure["stderr"] = err.getvalue()
        raise ExecError(failure)
    return {"result": _jsonable_result(ns.get("result")), "stdout": out.getvalue(), "stderr": err.getvalue()}
