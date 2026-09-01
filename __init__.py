"""Blix: pixel-art editing tools for the Blender image editor."""

import importlib
import sys

from . import (
    clipboard,
    dither,
    erase,
    guides,
    layers,
    line,
    mirror,
    overlay,
    palette,
    prefs,
    select,
    shapes,
    stacking,
    transform,
    ui,
    undo,
)

if "_modules" in locals():
    for _module in [mod for name, mod in sys.modules.items() if name.startswith(__name__ + ".")]:
        importlib.reload(_module)

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
    erase,
    line,
    shapes,
    dither,
    palette,
    stacking,
    ui,
)


def register() -> None:
    for module in _modules:
        module.register()


def unregister() -> None:
    for module in reversed(_modules):
        module.unregister()
