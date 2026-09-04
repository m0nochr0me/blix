"""Copy, paste and delete for the selected pixel region; paste lands on a new layer."""

from typing import TYPE_CHECKING, Any, cast

import bpy
import numpy as np

from . import layers, overlay, props, select, undo

if TYPE_CHECKING:
    from bpy.stub_internal.rna_enums import OperatorReturnItems

Contents = tuple[np.ndarray, np.ndarray, tuple[int, int]]

_clip: tuple[np.ndarray, np.ndarray, select.Rect] | None = None


def _has_image(context: bpy.types.Context) -> bool:
    image = select.edit_image(context)
    return image is not None and image.size[0] > 0 and image.size[1] > 0


def _has_selection(context: bpy.types.Context) -> bool:
    image = select.edit_image(context)
    return (
        image is not None
        and select.session.mask is not None
        and select.session.image_name == image.name
        and select.session.buffer is None
    )


def _system_copy(context: bpy.types.Context, pixels: np.ndarray) -> bool:
    """Put RGBA pixels on the system clipboard through a throwaway image."""
    height, width = pixels.shape[:2]
    image = bpy.data.images.new("blix_clipboard", width, height, alpha=True)
    select.write_pixels(image, pixels)
    with context.temp_override(edit_image=image):
        result = cast(Any, bpy.ops.image).clipboard_copy()
    bpy.data.images.remove(image)
    return "FINISHED" in result


def _system_paste(context: bpy.types.Context) -> np.ndarray | None:
    """Pixels of the system clipboard image; the native paste shows it, so the view is put back."""
    ops = cast(Any, bpy.ops.image)
    if not ops.clipboard_paste.poll():
        return None
    space = cast(bpy.types.SpaceImageEditor, context.space_data)
    shown = space.image
    ops.clipboard_paste()
    pasted = space.image
    space.image = shown
    if pasted is None or pasted == shown:
        return None
    pixels = select.read_pixels(pasted)
    bpy.data.images.remove(pasted)
    return pixels


def _fit(
    canvas: bpy.types.Image, origin: tuple[int, int], shape: tuple[int, ...]
) -> tuple[int, int]:
    height, width = shape[:2]
    return (
        max(0, min(origin[0], canvas.size[0] - width)),
        max(0, min(origin[1], canvas.size[1] - height)),
    )


def _contents(context: bpy.types.Context, canvas: bpy.types.Image) -> Contents | None:
    """Buffer, mask and origin to paste: the system clipboard image, or the Blix copy it holds."""
    pixels = _system_paste(context)
    if pixels is not None and (_clip is None or not np.array_equal(pixels, _clip[0])):
        sub = pixels[:, :, 3] > 0.0
        pixels[~sub] = 0.0
        height, width = sub.shape
        centered = ((canvas.size[0] - width) // 2, (canvas.size[1] - height) // 2)
        return pixels, sub, _fit(canvas, centered, sub.shape)
    if _clip is None:
        return None
    buffer, sub, rect = _clip
    return buffer, sub, _fit(canvas, (rect[0], rect[1]), sub.shape)


class BLIX_OT_select_copy(bpy.types.Operator):
    """Copy selected pixels of the active layer to the clipboard"""

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
        _system_copy(context, buffer)
        return {"FINISHED"}


class BLIX_OT_copy_flattened(bpy.types.Operator):
    """Copy the flattened image to the clipboard"""

    bl_idname = "blix.copy_flattened"
    bl_label = "Copy Flattened"
    bl_options = {"REGISTER", "INTERNAL"}

    @classmethod
    def poll(cls, context: bpy.types.Context) -> bool:
        return _has_image(context)

    def execute(self, context: bpy.types.Context) -> set[OperatorReturnItems]:
        image = select.edit_image(context)
        assert image is not None
        canvas = layers.resolve_canvas(context)
        if canvas is None:
            pixels = select.read_pixels(image)
        else:
            layers.sync_canvas(canvas)
            pixels = layers.composite_pixels(canvas)
        if not _system_copy(context, pixels):
            self.report({"WARNING"}, "Image clipboard is unavailable")
            return {"CANCELLED"}
        return {"FINISHED"}


class BLIX_OT_select_paste(bpy.types.Operator):
    """Paste the clipboard image as a new layer and start moving it; click or Enter confirms"""

    bl_idname = "blix.select_paste"
    bl_label = "Paste"
    bl_options = {"REGISTER", "INTERNAL"}

    @classmethod
    def poll(cls, context: bpy.types.Context) -> bool:
        return _has_image(context) and select.session.buffer is None

    def invoke(
        self, context: bpy.types.Context, event: bpy.types.Event
    ) -> set[OperatorReturnItems]:
        image = select.edit_image(context)
        assert image is not None
        canvas = layers.resolve_canvas(context) or image
        contents = _contents(context, canvas)
        if contents is None:
            self.report({"WARNING"}, "Nothing to paste")
            return {"CANCELLED"}
        buffer, sub, origin = contents
        mask = select.place(canvas, sub, origin)
        if not mask.any():
            self.report({"WARNING"}, "Clipboard image has no visible pixels")
            return {"CANCELLED"}
        if len(props.layers(canvas)) == 0:
            layers.init_layers(canvas)
        with layers._undo_step(context, canvas, "Blix Paste Layer"):
            layers.add_layer(canvas, layers.next_layer_number(canvas))
            layer = layers.active_layer(canvas)
            select.write_pixels(layer.image, select.place(canvas, buffer, origin))
            layers.composite(canvas)
        space = cast(bpy.types.SpaceImageEditor, context.space_data)
        if space.image != canvas:
            space.image = canvas
        session = select.session
        session.reset()
        session.image_name = canvas.name
        session.set_mask(mask)
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


_classes = (
    BLIX_OT_select_copy,
    BLIX_OT_copy_flattened,
    BLIX_OT_select_paste,
    BLIX_OT_select_delete,
)


def register() -> None:
    for cls in _classes:
        bpy.utils.register_class(cls)


def unregister() -> None:
    global _clip
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
    _clip = None
