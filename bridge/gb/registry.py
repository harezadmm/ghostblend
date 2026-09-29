"""Command table shared by every bridge module."""

COMMANDS = {}


def command(name):
    """Register `fn(args: dict) -> dict` as the handler for worker command `name`."""

    def deco(fn):
        if name in COMMANDS:
            raise RuntimeError(f"duplicate command {name}")
        COMMANDS[name] = fn
        return fn

    return deco
