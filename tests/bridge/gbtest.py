"""Test helpers: a throwaway session plus call()/ok() wrappers around gb.main.process."""
import itertools
import os
import tempfile

from gb import main as gbmain

SESSION = tempfile.mkdtemp(prefix="gbtest-")
# A work directory with a space and non-ASCII characters, like real user folders.
WORKDIR = os.path.join(SESSION, "work dir Ünïcode ñ")
os.makedirs(WORKDIR, exist_ok=True)
gbmain.bootstrap(SESSION, WORKDIR, keep=10, restore_latest=False)

# Mirrors the `mutating` flag of the Rust tool registry.
MUTATING = {
    "scene_new", "scene_open", "add_primitive", "transform", "object_delete", "object_duplicate",
    "modifier_add", "modifier_apply", "material_set", "import_model", "checkpoint_restore", "run_python",
}

_ids = itertools.count(1)


def call(cmd, _autosave=None, _atomic=None, **args):
    mutating = cmd in MUTATING if _autosave is None else _autosave
    req = {"id": next(_ids), "cmd": cmd, "args": args, "autosave": mutating,
           "atomic": mutating if _atomic is None else _atomic}
    return gbmain.process(req)


def ok(cmd, **args):
    resp = call(cmd, **args)
    if not resp.get("ok"):
        raise AssertionError(f"{cmd} failed: {resp.get('error')}")
    return resp["result"]


def err(cmd, **args):
    resp = call(cmd, **args)
    if resp.get("ok"):
        raise AssertionError(f"{cmd} unexpectedly succeeded: {resp.get('result')}")
    return resp


def py(code):
    """run_python and return the user's `result` value."""
    return ok("run_python", code=code)["result"]


def names():
    import bpy

    return sorted(o.name for o in bpy.context.scene.objects)
