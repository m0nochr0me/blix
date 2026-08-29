"""Blix: pixel-art editing tools for the Blender image editor."""

from . import guides, overlay, select, transform, ui

_modules = (overlay, guides, select, transform, ui)


def register() -> None:
    for module in _modules:
        module.register()


def unregister() -> None:
    for module in reversed(_modules):
        module.unregister()
