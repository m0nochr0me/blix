"""Blix: pixel-art editing tools for the Blender image editor."""

from . import dither, guides, layers, overlay, prefs, select, transform, ui, undo

_modules = (prefs, undo, overlay, guides, select, transform, layers, dither, ui)


def register() -> None:
    for module in _modules:
        module.register()


def unregister() -> None:
    for module in reversed(_modules):
        module.unregister()
