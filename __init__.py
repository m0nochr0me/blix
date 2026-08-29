"""Blix: pixel-art editing tools for the Blender image editor."""

from . import ui

_modules = (ui,)


def register() -> None:
    for module in _modules:
        module.register()


def unregister() -> None:
    for module in reversed(_modules):
        module.unregister()
