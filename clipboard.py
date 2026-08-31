"""Copy, paste and delete for the selected pixel region on the active layer."""

from typing import TYPE_CHECKING, Any, cast

import bpy
import numpy as np

from . import layers, overlay, props, select, undo

if TYPE_CHECKING:
    from bpy.stub_internal.rna_enums import OperatorReturnItems

_clip: tuple[np.ndarray, np.ndarray, select.Rect] | None = None


def _has_selection(context: bpy.types.Context) -> bool:
    image = select.edit_image(context)
    return (
        image is not None
        and select.session.mask is not None
        and select.session.image_name == image.name
        and select.session.buffer is None
    )


class BLIX_OT_select_copy(bpy.types.Operator):
    """Copy selected pixels of the active layer to the Blix clipboard"""

    bl_idname = "blix.select_copy"
    bl_label = "Copy Selection"
    bl_options = {"REGISTER", "INTERNAL"}

    @classmethod
    def poll(cls, context: bpy.types.Context) -> bool:
        return _has_selection(context)

    def execute(self, context: bpy.types.Context) -> set[OperatorReturnItems]:
        global _clip
        image = select.edit_image(context)
        mask = select.session.mask
        rect = select.session.rect
        assert image is not None and mask is not None and rect is not None
        buffer, sub = select.lift(image, mask, rect)
        _clip = (buffer, sub, rect)
        return {"FINISHED"}


class BLIX_OT_select_paste(bpy.types.Operator):
    """Paste clipboard pixels as a floating selection; click or Enter commits, Esc cancels"""

    bl_idname = "blix.select_paste"
    bl_label = "Paste"
    bl_options = {"REGISTER", "INTERNAL"}

    @classmethod
    def poll(cls, context: bpy.types.Context) -> bool:
        image = select.edit_image(context)
        return (
            _clip is not None
            and image is not None
            and image.size[0] > 0
            and image.size[1] > 0
            and select.session.buffer is None
            and not select.target_locked(image)
        )

    def invoke(
        self, context: bpy.types.Context, event: bpy.types.Event
    ) -> set[OperatorReturnItems]:
        assert _clip is not None
        image = select.edit_image(context)
        assert image is not None
        buffer, sub, rect = _clip
        height, width = sub.shape
        ox = max(0, min(rect[0], image.size[0] - width))
        oy = max(0, min(rect[1], image.size[1] - height))
        session = select.session
        session.reset()
        session.image_name = image.name
        session.mask = select.place_mask(image, sub, (ox, oy))
        session.rect = (ox, oy, ox + width, oy + height)
        session.buffer = buffer.copy()
        session.float_mask = sub.copy()
        session.float_outline = select.mask_outline(sub)
        session.texture = select.make_texture(buffer)
        session.offset = (0, 0)
        session.preview_offset = (0, 0)
        session.preview_quad = select.rect_quad(session.rect)
        session.keep_source = True
        overlay.tag_redraw(context)
        return cast(Any, bpy.ops).blix.select_move("INVOKE_DEFAULT")


class BLIX_OT_select_delete(bpy.types.Operator):
    """Delete selected pixels from the active layer"""

    bl_idname = "blix.select_delete"
    bl_label = "Delete Selection"
    bl_options = {"REGISTER", "INTERNAL"}

    @classmethod
    def poll(cls, context: bpy.types.Context) -> bool:
        return select.can_float(context)

    def execute(self, context: bpy.types.Context) -> set[OperatorReturnItems]:
        image = select.edit_image(context)
        mask = select.session.mask
        assert image is not None and mask is not None
        target = select.pixel_target(image)
        pixels = select.read_pixels(target)
        pixels[mask] = 0.0
        undo.record(context, image)
        select.write_pixels(target, pixels)
        if len(props.layers(image)):
            layers.composite(image)
        undo.record(context, image)
        overlay.tag_redraw(context)
        return {"FINISHED"}


_classes = (BLIX_OT_select_copy, BLIX_OT_select_paste, BLIX_OT_select_delete)


def register() -> None:
    for cls in _classes:
        bpy.utils.register_class(cls)


def unregister() -> None:
    global _clip
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
    _clip = None
