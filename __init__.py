"""Blix: pixel-art editing tools for the Blender image editor."""

from . import dither, guides, layers, overlay, select, transform, ui

_modules = (overlay, guides, select, transform, layers, dither, ui)


def register() -> None:
    for module in _modules:
        module.register()


def unregister() -> None:
    for module in reversed(_modules):
        module.unregister()
