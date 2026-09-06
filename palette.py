"""Hex palette import (sRGB codes decoded to scene-linear swatches) and the RMB palette popup."""

import re
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import bpy
import numpy as np
from bpy_extras.io_utils import ImportHelper

from . import paint, prefs

if TYPE_CHECKING:
    from bpy.stub_internal.rna_enums import OperatorReturnItems

_CODE = re.compile(r"[0-9a-fA-F]{6}")
_XYZ = np.array(
    [
        [0.4124564, 0.3575761, 0.1804375],
        [0.2126729, 0.7151522, 0.0721750],
        [0.0193339, 0.1191920, 0.9503041],
    ],
    dtype=np.float32,
)
_WHITE = np.array([0.95047, 1.0, 1.08883], dtype=np.float32)


def parse_hex(text: str) -> tuple[list[np.ndarray], int]:
    """Scene-linear colors from one hex code per line, plus count of unparsed lines."""
    colors: list[np.ndarray] = []
    skipped = 0
    for line in text.splitlines():
        code = line.strip().removeprefix("#")
        if not code:
            continue
        if not _CODE.fullmatch(code):
            skipped += 1
            continue
        value = int(code, 16)
        channels = np.array([value >> 16 & 0xFF, value >> 8 & 0xFF, value & 0xFF], dtype=np.float32)
        colors.append(paint.srgb_decode(channels / 255.0))
    return colors, skipped


def active(context: bpy.types.Context) -> bpy.types.Palette | None:
    tool_settings = context.tool_settings
    if tool_settings is None or tool_settings.image_paint is None:
        return None
    return tool_settings.image_paint.palette


def swatches(palette: bpy.types.Palette) -> np.ndarray:
    """Palette colours as sRGB rows, the space byte image pixels are stored in."""
    colors = [list(cast(Any, color).color) for color in palette.colors]
    return paint.srgb_encode(np.array(colors, dtype=np.float32).reshape(-1, 3))


def _lab(srgb: np.ndarray) -> np.ndarray:
    xyz = paint.srgb_decode(srgb) @ _XYZ.T / _WHITE
    f = np.where(xyz > 0.008856, np.cbrt(xyz), 7.787 * xyz + 16 / 116)
    return np.stack(
        [116 * f[:, 1] - 16, 500 * (f[:, 0] - f[:, 1]), 200 * (f[:, 1] - f[:, 2])], axis=1
    )


def nearest(colors: np.ndarray, swatches: np.ndarray) -> np.ndarray:
    """Index of the closest swatch per sRGB colour row, CIELAB distance so hue outweighs value."""
    lab, targets = _lab(colors), _lab(swatches)
    distance = ((lab[:, None, :] - targets[None, :, :]) ** 2).sum(axis=2)
    return distance.argmin(axis=1)


class BLIX_OT_palette_import(bpy.types.Operator, ImportHelper):
    """Load a .hex palette file: one sRGB hex code per line"""

    bl_idname = "blix.palette_import"
    bl_label = "Import Hex Palette"
    bl_options = {"REGISTER", "UNDO"}

    filename_ext = ".hex"
    filter_glob: bpy.props.StringProperty(default="*.hex", options={"HIDDEN"})

    def execute(self, context: bpy.types.Context) -> set[OperatorReturnItems]:
        path = Path(cast(Any, self).filepath)
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError as error:
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}

        colors, skipped = parse_hex(text)
        if not colors:
            self.report({"ERROR"}, f"No hex codes in {path.name}")
            return {"CANCELLED"}

        palette = bpy.data.palettes.new(path.stem)
        for color in colors:
            cast(Any, palette.colors.new()).color = color
        tool_settings = context.tool_settings
        if tool_settings is not None and tool_settings.image_paint is not None:
            tool_settings.image_paint.palette = palette

        report = f"{len(colors)} colors from {path.name}"
        if skipped:
            report += f", {skipped} lines skipped"
        self.report({"INFO"}, report)
        return {"FINISHED"}


def draw_palette(layout: bpy.types.UILayout, image_paint: Any) -> None:
    layout.template_ID(image_paint, "palette", new="palette.new")
    if image_paint.palette is not None:
        layout.template_palette(image_paint, "palette")


class BLIX_PT_palette_popup(bpy.types.Panel):
    bl_space_type = "IMAGE_EDITOR"
    bl_region_type = "WINDOW"
    bl_label = "Palette"

    def draw(self, context: bpy.types.Context) -> None:
        layout = self.layout
        tool_settings = context.tool_settings
        assert layout is not None
        assert tool_settings is not None
        draw_palette(layout, tool_settings.image_paint)


class BLIX_OT_palette_popup(bpy.types.Operator):
    """Pop up the palette under the cursor"""

    bl_idname = "blix.palette_popup"
    bl_label = "Palette Popup"
    bl_options = {"INTERNAL"}

    @classmethod
    def poll(cls, context: bpy.types.Context) -> bool:
        return prefs.rmb_palette()

    def execute(self, context: bpy.types.Context) -> set[OperatorReturnItems]:
        cast(Any, bpy.ops.wm).call_panel("INVOKE_DEFAULT", name=BLIX_PT_palette_popup.__name__)
        return {"FINISHED"}


_classes = (BLIX_OT_palette_import, BLIX_PT_palette_popup, BLIX_OT_palette_popup)
_keymaps: list[tuple[bpy.types.KeyMap, bpy.types.KeyMapItem]] = []


def _register_keymap() -> None:
    window_manager = bpy.context.window_manager
    if window_manager is None:
        return
    keyconfig = window_manager.keyconfigs.addon
    if keyconfig is None:
        return
    keymap = keyconfig.keymaps.new(name="Image Paint", space_type="EMPTY")
    item = keymap.keymap_items.new(BLIX_OT_palette_popup.bl_idname, "RIGHTMOUSE", "PRESS")
    _keymaps.append((keymap, item))


def _unregister_keymap() -> None:
    for keymap, item in _keymaps:
        keymap.keymap_items.remove(item)
    _keymaps.clear()


def register() -> None:
    for cls in _classes:
        bpy.utils.register_class(cls)
    _register_keymap()


def unregister() -> None:
    _unregister_keymap()
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
