"""Autosave ring, rollback, and named checkpoints.

Background mode has no undo, so every successful mutating command saves a copy
of the whole file to `autosave/NNNNNN.blend`. A failed mutating command reopens
the latest autosave, which is the state right before that command.
"""
import datetime
import json
import os
import re
import time

import bpy

from . import registry, util

S = {
    "dir": None,           # session directory
    "keep": 10,            # autosaves kept on disk
    "seq": 0,              # latest autosave sequence number
    "logical_path": None,  # the .blend the user opened or saved, if any
    "saved_seq": None,     # autosave seq that matches the last explicit save/open
}

_SEQ_RE = re.compile(r"(\d{6})\.blend")


def _now():
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


def _autosave_dir():
    return os.path.join(S["dir"], "autosave")


def _ckpt_dir():
    return os.path.join(S["dir"], "checkpoints")


def _seq_path(seq):
    return os.path.join(_autosave_dir(), f"{seq:06d}.blend")


def _index_path():
    return os.path.join(_autosave_dir(), "index.jsonl")


def init(session_dir, keep):
    S["dir"] = session_dir
    S["keep"] = max(1, int(keep))
    os.makedirs(_autosave_dir(), exist_ok=True)
    os.makedirs(_ckpt_dir(), exist_ok=True)
    seqs = existing_seqs()
    S["seq"] = seqs[-1] if seqs else 0


def existing_seqs():
    out = []
    try:
        names = os.listdir(_autosave_dir())
    except FileNotFoundError:
        return out
    for n in names:
        m = _SEQ_RE.fullmatch(n)
        if m:
            out.append(int(m.group(1)))
    return sorted(out)


def latest():
    seqs = existing_seqs()
    return seqs[-1] if seqs else None


def _read_index():
    entries = {}
    try:
        with open(_index_path(), encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    e = json.loads(line)
                except ValueError:
                    continue
                entries[e.get("seq")] = e
    except FileNotFoundError:
        pass
    return entries


def save_copy(path):
    """Write the current state to `path` without changing the open file."""
    util.ensure_parent_dir(path)
    bpy.ops.wm.save_as_mainfile(filepath=path, copy=True, compress=False, relative_remap=True,
                                check_existing=False)


def open_blend(path):
    bpy.ops.wm.open_mainfile(filepath=path, load_ui=False)


def autosave(cmd, label=None):
    t0 = time.perf_counter()
    seq = S["seq"] + 1
    path = _seq_path(seq)
    save_copy(path)
    S["seq"] = seq
    entry = {"seq": seq, "time": _now(), "cmd": cmd, "label": label, "file": S["logical_path"],
             "saved_seq": S["saved_seq"], "objects": len(bpy.context.scene.objects)}
    with open(_index_path(), "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    _prune()
    return {"seq": seq, "path": path, "ms": int((time.perf_counter() - t0) * 1000)}


def _prune():
    seqs = existing_seqs()
    extra = len(seqs) - S["keep"]
    for s in seqs[:max(0, extra)]:
        try:
            os.remove(_seq_path(s))
        except OSError:
            pass


def restore_seq(seq):
    path = _seq_path(seq)
    if not os.path.isfile(path):
        raise util.UserError(f"Autosave #{seq} is no longer available",
                             hint=f"Available autosaves: {existing_seqs()}", kind="NotFound")
    open_blend(path)
    entry = _read_index().get(seq) or {}
    S["logical_path"] = entry.get("file")
    S["saved_seq"] = entry.get("saved_seq")
    return {"seq": seq, "path": path}


def rollback():
    """Reopen the latest autosave. Returns None when there is nothing to roll back to."""
    seq = S["seq"]
    if seq <= 0 or not os.path.isfile(_seq_path(seq)):
        return None
    return restore_seq(seq)


def unsaved_changes():
    return S["logical_path"] is None or S["saved_seq"] != S["seq"]


# ---------------------------------------------------------------- checkpoints


def _ckpt_index_path():
    return os.path.join(_ckpt_dir(), "index.json")


def _read_ckpt_index():
    try:
        with open(_ckpt_index_path(), encoding="utf-8") as f:
            data = json.load(f)
            data.setdefault("next", 1)
            data.setdefault("items", [])
            return data
    except (FileNotFoundError, ValueError):
        return {"next": 1, "items": []}


def _write_ckpt_index(data):
    tmp = _ckpt_index_path() + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, _ckpt_index_path())


@registry.command("checkpoint_save")
def checkpoint_save(args):
    label = (args.get("label") or "").strip()
    idx = _read_ckpt_index()
    cid = f"c{idx['next']}"
    slug = re.sub(r"[^A-Za-z0-9_-]+", "-", label).strip("-")[:40]
    fname = f"{cid}-{slug}.blend" if slug else f"{cid}.blend"
    path = os.path.join(_ckpt_dir(), fname)
    save_copy(path)
    item = {"id": cid, "label": label or None, "file": fname, "time": _now(),
            "scene_file": S["logical_path"], "saved_seq": S["saved_seq"], "autosave_seq": S["seq"],
            "objects": len(bpy.context.scene.objects)}
    idx["items"].append(item)
    idx["next"] += 1
    _write_ckpt_index(idx)
    return {"id": cid, "label": item["label"], "path": path, "objects": item["objects"]}


@registry.command("checkpoint_list")
def checkpoint_list(args):
    idx = _read_ckpt_index()
    checkpoints = []
    for it in idx["items"]:
        path = os.path.join(_ckpt_dir(), it["file"])
        if os.path.isfile(path):
            checkpoints.append({k: it.get(k) for k in ("id", "label", "time", "objects")})
    index = _read_index()
    autosaves = []
    for seq in reversed(existing_seqs()):
        e = index.get(seq) or {}
        autosaves.append({"seq": seq, "cmd": e.get("cmd"), "label": e.get("label"), "time": e.get("time"),
                          "objects": e.get("objects"), "steps_back": S["seq"] - seq})
    return {"current_seq": S["seq"], "checkpoints": checkpoints, "autosaves": autosaves}


@registry.command("checkpoint_restore")
def checkpoint_restore(args):
    given = [k for k in ("checkpoint", "autosave_seq", "steps") if args.get(k) is not None]
    if len(given) != 1:
        raise util.UserError("Pass exactly one of: checkpoint, autosave_seq, steps",
                             hint="steps=1 undoes the last change; checkpoint_list shows what is available")
    if "checkpoint" in given:
        cid = str(args["checkpoint"])
        idx = _read_ckpt_index()
        item = next((it for it in idx["items"] if it["id"] == cid), None)
        if item is None:
            ids = [it["id"] for it in idx["items"]]
            raise util.UserError(f"No checkpoint {cid!r}", hint=f"Checkpoints: {ids}" if ids else
                                 "No checkpoints yet; create one with checkpoint_save", kind="NotFound")
        path = os.path.join(_ckpt_dir(), item["file"])
        if not os.path.isfile(path):
            raise util.UserError(f"Checkpoint file is missing: {path}", kind="NotFound")
        open_blend(path)
        S["logical_path"] = item.get("scene_file")
        S["saved_seq"] = None
        source = {"checkpoint": cid, "label": item.get("label")}
    else:
        if "steps" in given:
            steps = int(args["steps"])
            if steps < 1:
                raise util.UserError("steps must be at least 1")
            seq = S["seq"] - steps
        else:
            seq = int(args["autosave_seq"])
        if seq not in existing_seqs():
            raise util.UserError(f"Autosave #{seq} is not available", kind="NotFound",
                                 hint=f"Available autosaves: {existing_seqs()} (current is #{S['seq']})")
        restore_seq(seq)
        S["saved_seq"] = None
        source = {"autosave_seq": seq}
    from . import scene
    return {"restored": source, "scene": scene.scene_summary(limit=50)}
