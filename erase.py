"""Optional Ctrl+LMB behavior: run the native brush stroke with Erase Alpha blend."""

from typing import TYPE_CHECKING, Any, cast

import bpy

from . import mirror, prefs, select

if TYPE_CHECKING:
    from bpy.stub_internal.rna_enums import OperatorReturnItems

POLL = 0.05
_saved: str | None = None


def _brush(context: bpy.types.Context) -> bpy.types.Brush | None:
    tool_settings = context.tool_settings
    paint = tool_settings.image_paint if tool_settings is not None else None
    return paint.brush if paint is not None else None


def _tick() -> float | None:
    global _saved
    if _saved is None:
        return None
    if mirror.busy():
        return POLL
    brush = _brush(bpy.context)
    if brush is not None:
        cast(Any, brush).blend = _saved
    _saved = None
    return None


class BLIX_OT_ctrl_erase(bpy.types.Operator):
    """Paint the stroke with Erase Alpha blend, restoring the brush blend at stroke end"""

    bl_idname = "blix.ctrl_erase"
    bl_label = "Ctrl Erase Stroke"
    bl_options = {"INTERNAL"}

    @classmethod
    def poll(cls, context: bpy.types.Context) -> bool:
        if not prefs.ctrl_erase():
            return False
        image = select.edit_image(context)
        return image is not None and image.size[0] > 0 and image.size[1] > 0

    def invoke(
        self, context: bpy.types.Context, event: bpy.types.Event
    ) -> set[OperatorReturnItems]:
        global _saved
        brush = _brush(context)
        if brush is None or not cast(Any, bpy.ops.paint.image_paint).poll():
            return {"PASS_THROUGH"}
        if _saved is None:
            _saved = brush.blend
        brush.blend = "ERASE_ALPHA"
        bpy.ops.paint.image_paint("INVOKE_DEFAULT", mode="NORMAL")
        if not bpy.app.timers.is_registered(_tick):
            bpy.app.timers.register(_tick, first_interval=POLL)
        return {"FINISHED"}


_keymaps: list[tuple[bpy.types.KeyMap, bpy.types.KeyMapItem]] = []


def _register_keymap() -> None:
    window_manager = bpy.context.window_manager
    if window_manager is None:
        return
    keyconfig = window_manager.keyconfigs.addon
    if keyconfig is None:
        return
    keymap = keyconfig.keymaps.new(name="Image Paint", space_type="EMPTY")
    item = keymap.keymap_items.new(BLIX_OT_ctrl_erase.bl_idname, "LEFTMOUSE", "PRESS", ctrl=True)
    _keymaps.append((keymap, item))


def _unregister_keymap() -> None:
    for keymap, item in _keymaps:
        keymap.keymap_items.remove(item)
    _keymaps.clear()


def register() -> None:
    global _saved
    _saved = None
    bpy.utils.register_class(BLIX_OT_ctrl_erase)
    _register_keymap()


def unregister() -> None:
    global _saved
    _unregister_keymap()
    if bpy.app.timers.is_registered(_tick):
        bpy.app.timers.unregister(_tick)
    _saved = None
    bpy.utils.unregister_class(BLIX_OT_ctrl_erase)
