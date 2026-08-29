"""Blix: pixel-art editing tools for the Blender image editor."""

from . import dither, guides, layers, mirror, overlay, prefs, select, shapes, transform, ui, undo

_modules = (prefs, undo, overlay, mirror, guides, select, transform, layers, shapes, dither, ui)


def register() -> None:
    for module in _modules:
        module.register()


def unregister() -> None:
    for module in reversed(_modules):
        module.unregister()
