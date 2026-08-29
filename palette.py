"""Hex palette import: sRGB codes decoded to the scene-linear swatches Blender stores."""

import re
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import bpy
import numpy as np
from bpy_extras.io_utils import ImportHelper

from . import paint

if TYPE_CHECKING:
    from bpy.stub_internal.rna_enums import OperatorReturnItems

_CODE = re.compile(r"[0-9a-fA-F]{6}")


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
            palette.colors.new().color = color
        tool_settings = context.tool_settings
        if tool_settings is not None:
            tool_settings.image_paint.palette = palette

        report = f"{len(colors)} colors from {path.name}"
        if skipped:
            report += f", {skipped} lines skipped"
        self.report({"INFO"}, report)
        return {"FINISHED"}


def register() -> None:
    bpy.utils.register_class(BLIX_OT_palette_import)


def unregister() -> None:
    bpy.utils.unregister_class(BLIX_OT_palette_import)
