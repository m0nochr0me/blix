"""Add-on preferences: overlay colors, ruler size, keymap editing."""

from typing import Any

import bpy
import rna_keymap_ui

PACKAGE = __package__ or ""
RGBA = tuple[float, float, float, float]

DEFAULTS: dict[str, RGBA] = {
    "guide_color": (0.15, 0.55, 1.0, 0.85),
    "grid_color": (0.15, 0.15, 0.15, 0.55),
    "ruler_background": (0.10, 0.10, 0.10, 0.92),
    "ruler_text": (0.70, 0.70, 0.70, 1.0),
    "mirror_color": (1.0, 0.35, 0.35, 0.6),
}
RULER_SIZE = 20


def get() -> Any | None:
    preferences = bpy.context.preferences
    if preferences is None:
        return None
    entry = preferences.addons.get(PACKAGE)
    return entry.preferences if entry is not None else None


def color(name: str) -> RGBA:
    settings = get()
    if settings is None:
        return DEFAULTS[name]
    red, green, blue, alpha = getattr(settings, name)
    return (red, green, blue, alpha)


def ruler_size() -> int:
    settings = get()
    return RULER_SIZE if settings is None else int(settings.ruler_size)


def ctrl_erase() -> bool:
    settings = get()
    return False if settings is None else bool(settings.ctrl_erase)


def _color_property(name: str, label: str) -> Any:
    return bpy.props.FloatVectorProperty(
        name=label,
        subtype="COLOR",
        size=4,
        min=0.0,
        max=1.0,
        default=DEFAULTS[name],
    )


class BlixPreferences(bpy.types.AddonPreferences):
    bl_idname = PACKAGE

    guide_color: _color_property("guide_color", "Guide")
    grid_color: _color_property("grid_color", "Grid")
    ruler_background: _color_property("ruler_background", "Ruler Background")
    ruler_text: _color_property("ruler_text", "Ruler Text")
    mirror_color: _color_property("mirror_color", "Mirror Axis")
    ruler_size: bpy.props.IntProperty(
        name="Ruler Size",
        description="Ruler band width in pixels, before UI scale",
        default=RULER_SIZE,
        min=12,
        max=64,
    )
    ctrl_erase: bpy.props.BoolProperty(
        name="Ctrl+LMB Erases",
        description="Paint with Erase Alpha blend while Ctrl is held, instead of background color",
        default=False,
    )

    def draw(self, context: bpy.types.Context) -> None:
        layout = self.layout
        assert layout is not None
        column = layout.column(align=True)
        for name in DEFAULTS:
            column.prop(self, name)
        layout.prop(self, "ruler_size")
        layout.prop(self, "ctrl_erase")
        _draw_keymap(layout, context)


def _draw_keymap(layout: bpy.types.UILayout, context: bpy.types.Context) -> None:
    window_manager = context.window_manager
    keyconfig = window_manager.keyconfigs.addon if window_manager is not None else None
    if keyconfig is None:
        return
    layout.label(text="Keymap")
    box = layout.box()
    for keymap in keyconfig.keymaps:
        items = [item for item in keymap.keymap_items if item.idname.startswith("blix.")]
        if not items:
            continue
        box.label(text=keymap.name)
        for item in items:
            rna_keymap_ui.draw_kmi([], keyconfig, keymap, item, box, 0)


def register() -> None:
    bpy.utils.register_class(BlixPreferences)


def unregister() -> None:
    bpy.utils.unregister_class(BlixPreferences)
