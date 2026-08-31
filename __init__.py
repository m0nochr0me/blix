"""Blix: pixel-art editing tools for the Blender image editor."""

from . import (
    clipboard,
    dither,
    guides,
    layers,
    mirror,
    overlay,
    palette,
    prefs,
    select,
    shapes,
    transform,
    ui,
    undo,
)

_modules = (
    prefs,
    undo,
    overlay,
    mirror,
    guides,
    select,
    transform,
    clipboard,
    layers,
    shapes,
    dither,
    palette,
    ui,
)


def register() -> None:
    for module in _modules:
        module.register()


def unregister() -> None:
    for module in reversed(_modules):
        module.unregister()
