"""Text layers: layer pixels rendered from editable text, font and size until rasterized."""

import hashlib
import math
import os
import shutil
from typing import TYPE_CHECKING, Any, cast

import blf
import bpy
import imbuf
import numpy as np
from bpy.app.handlers import persistent

from . import layers, overlay, paint, prefs, props, select, transform, undo

if TYPE_CHECKING:
    from bpy.stub_internal.rna_enums import OperatorReturnItems

TOOL_ID = "blix.text"
DEFAULT_FONT = "DejaVuSansMono.woff2"
HIT_SLACK = 4.0
_NUDGES = {
    "LEFT_ARROW": (-1, 0),
    "RIGHT_ARROW": (1, 0),
    "UP_ARROW": (0, -1),
    "DOWN_ARROW": (0, 1),
}

_fonts: dict[str, int] = {}


def _private_copy(key: str, source: str | None, data: bytes | None) -> str:
    """Session copy of a font file: blf shares fonts by path, and the UI owns the bundled ones."""
    digest = hashlib.sha1(key.encode()).hexdigest()[:16]
    path = os.path.join(bpy.app.tempdir, f"blix_font_{digest}")
    if os.path.exists(path):
        return path
    if source is not None:
        shutil.copyfile(source, path)
        return path
    with open(path, "wb") as handle:
        handle.write(data or b"")
    return path


def _default_path() -> str:
    fonts = cast(Any, bpy.utils).system_resource("DATAFILES", path="fonts")
    source = os.path.join(fonts, DEFAULT_FONT)
    return _private_copy(source, source, None)


def _font_path(font: Any) -> str:
    if font is None:
        return _default_path()
    source = bpy.path.abspath(font.filepath)
    if os.path.isfile(source):
        return _private_copy(source, source, None)
    if font.packed_file is None:
        return _default_path()
    return _private_copy(f"{font.name}:{font.filepath}", None, font.packed_file.data)


def font_id(font: Any) -> int:
    """blf font for a VectorFont, falling back to the bundled default when unset or unreadable."""
    path = _font_path(font)
    if path not in _fonts:
        _fonts[path] = blf.load(path)
    loaded = _fonts[path]
    if loaded >= 0 or font is None:
        return loaded
    return font_id(None)


def render(canvas: bpy.types.Image, layer: Any) -> np.ndarray:
    """Canvas-sized RGBA of the layer text: monochrome glyphs, baseline starting at text_origin."""
    width, height = canvas.size
    size = int(layer.text_size)
    fontid = font_id(layer.font)
    blf.enable(fontid, blf.MONOCHROME)
    blf.size(fontid, size)
    extent = math.ceil(blf.dimensions(fontid, layer.text)[0]) + 2 * size
    buffer = imbuf.new((extent, 3 * size))
    blf.color(fontid, 1.0, 1.0, 1.0, 1.0)
    blf.position(fontid, size, size, 0)
    with cast(Any, blf).bind_imbuf(fontid, buffer):
        blf.draw_buffer(fontid, layer.text)
    blf.disable(fontid, blf.MONOCHROME)
    with cast(Any, buffer).with_buffer() as view:
        coverage = np.asarray(view)[:, :, 3] > 127
    buffer.free()
    ox, oy = layer.text_origin
    placed = select.place(canvas, coverage, (ox - size, height - 1 - oy - size))
    pixels = np.zeros((height, width, 4), dtype=np.float32)
    pixels[placed] = np.array(layer.text_color, dtype=np.float32)
    return pixels


def refresh(canvas: bpy.types.Image, layer: Any) -> None:
    """Rerender the layer image from its text properties and recomposite the canvas."""
    if layer.image is None or tuple(layer.image.size) != tuple(canvas.size):
        return
    layers.sync_canvas(canvas)
    select.write_pixels(layer.image, render(canvas, layer))
    layers.composite(canvas)
    overlay.tag_redraw(bpy.context)


def bounds(layer: Any) -> select.Rect | None:
    return select.mask_bbox(select.read_pixels(layer.image)[:, :, 3] > 0.0)


def active_text(context: bpy.types.Context) -> tuple[bpy.types.Image, Any] | None:
    """Canvas and its active layer when that layer is a text layer."""
    canvas = layers.resolve_canvas(context)
    if canvas is None:
        return None
    layer = layers.active_layer(canvas)
    if layer is None or layer.image is None or not layer.is_text:
        return None
    return canvas, layer


def _tool_active(context: bpy.types.Context) -> bool:
    workspace = context.workspace
    if workspace is None:
        return False
    tool = workspace.tools.from_space_image_mode("PAINT", create=False)
    return tool is not None and tool.idname == TOOL_ID


def _hit(
    region: bpy.types.Region, image: bpy.types.Image, layer: Any, event: bpy.types.Event
) -> bool:
    rect = bounds(layer)
    affine = select._image_affine(region, image)
    if rect is None or affine is None:
        return False
    slack = HIT_SLACK / max(affine[0], 1e-6)
    x0, y0, x1, y1 = rect
    mx, my = select.mouse_pixel(region, image, event)
    return x0 - slack <= mx < x1 + slack and y0 - slack <= my < y1 + slack


def _draw(region: bpy.types.Region, image: bpy.types.Image) -> None:
    if not _tool_active(bpy.context):
        return
    canvas = image if len(props.layers(image)) else props.canvas_of(image)
    if canvas is None or tuple(canvas.size) != tuple(image.size):
        return
    layer = layers.active_layer(canvas)
    if layer is None or layer.image is None or not layer.is_text:
        return
    rect = bounds(layer)
    if rect is None:
        return
    quad = select._to_region(region, image, select.rect_quad(rect))
    overlay.draw_lines(select._loop_points(quad), prefs.color("outline_color"))


@persistent
def _undo_refresh(*_args: Any) -> None:
    """Undo moved text properties; rerender text layers so their pixels follow, with no step."""
    healed = False
    for canvas in bpy.data.images:
        changed = False
        for layer in props.layers(canvas):
            if not layer.is_text or layer.image is None:
                continue
            if tuple(layer.image.size) != tuple(canvas.size):
                continue
            pixels = render(canvas, layer)
            if np.array_equal(pixels, select.read_pixels(layer.image)):
                continue
            select.write_pixels(layer.image, pixels)
            changed = True
        if changed and layers._heal(canvas):
            healed = True
    if healed:
        overlay.tag_redraw(bpy.context)


class BLIX_OT_text_add(bpy.types.Operator):
    """Add a text layer with its baseline at the click point, or start moving the clicked one"""

    bl_idname = "blix.text_add"
    bl_label = "Add Text"
    bl_options = {"REGISTER", "INTERNAL"}

    text: bpy.props.StringProperty(name="Text", default="Text")
    size: bpy.props.IntProperty(name="Size", default=16, min=1, soft_max=256)
    origin: bpy.props.IntVectorProperty(size=2, options={"SKIP_SAVE", "HIDDEN"})

    @classmethod
    def poll(cls, context: bpy.types.Context) -> bool:
        image = select.edit_image(context)
        return image is not None and image.size[0] > 0 and image.size[1] > 0

    def invoke(
        self, context: bpy.types.Context, event: bpy.types.Event
    ) -> set[OperatorReturnItems]:
        image = select.edit_image(context)
        region = context.region
        assert image is not None and region is not None
        found = active_text(context)
        if found is not None and _hit(region, image, found[1], event):
            return cast(Any, bpy.ops).blix.text_move("INVOKE_DEFAULT", drag=True)
        x, y = select.mouse_pixel(region, image, event)
        self.origin = (math.floor(x), image.size[1] - 1 - math.floor(y))
        window_manager = context.window_manager
        assert window_manager is not None
        return window_manager.invoke_props_dialog(self)

    def execute(self, context: bpy.types.Context) -> set[OperatorReturnItems]:
        image = select.edit_image(context)
        assert image is not None
        canvas = layers.resolve_canvas(context) or image
        if not self.properties.is_property_set("origin"):
            self.origin = (canvas.size[0] // 2, canvas.size[1] // 2)
        if len(props.layers(canvas)) == 0:
            layers.init_layers(canvas)
        color = paint.brush_colors(context)[0]
        with layers._undo_step(context, canvas, "Blix Add Text"):
            layers.add_layer(canvas, layers.next_layer_number(canvas))
            layer = layers.active_layer(canvas)
            layer.label = self.text
            layer.text = self.text
            layer.text_size = self.size
            layer.text_color = color.tolist()
            layer.text_origin = self.origin
            layer.is_text = True
            refresh(canvas, layer)
        space = cast(bpy.types.SpaceImageEditor, context.space_data)
        if space.image != canvas:
            space.image = canvas
        overlay.tag_redraw(context)
        return {"FINISHED"}


class BLIX_OT_text_move(bpy.types.Operator):
    """Move the active text layer; Ctrl locks the axis, arrows nudge, click or Enter confirms"""

    bl_idname = "blix.text_move"
    bl_label = "Move Text"
    bl_options = {"REGISTER", "INTERNAL"}

    drag: bpy.props.BoolProperty(default=False, options={"SKIP_SAVE", "HIDDEN"})

    _start: tuple[float, float]
    _origin: tuple[int, int]
    _nudge: list[int]

    @classmethod
    def poll(cls, context: bpy.types.Context) -> bool:
        return active_text(context) is not None

    def invoke(
        self, context: bpy.types.Context, event: bpy.types.Event
    ) -> set[OperatorReturnItems]:
        image = select.edit_image(context)
        region = context.region
        found = active_text(context)
        assert image is not None and region is not None and found is not None
        layer = found[1]
        self._origin = (layer.text_origin[0], layer.text_origin[1])
        self._start = select.mouse_pixel(region, image, event)
        self._nudge = [0, 0]
        window_manager = context.window_manager
        assert window_manager is not None
        window_manager.modal_handler_add(self)
        return {"RUNNING_MODAL"}

    def modal(self, context: bpy.types.Context, event: bpy.types.Event) -> set[OperatorReturnItems]:
        image = select.edit_image(context)
        region = context.region
        found = active_text(context)
        if image is None or region is None or found is None:
            return {"CANCELLED"}
        canvas, layer = found

        if event.type == "MOUSEMOVE":
            mx, my = select.mouse_pixel(region, image, event)
            dx, dy = mx - self._start[0], my - self._start[1]
            if event.ctrl:
                if abs(dx) >= abs(dy):
                    dy = 0.0
                else:
                    dx = 0.0
            layer.text_origin = (
                round(self._origin[0] + dx) + self._nudge[0],
                round(self._origin[1] - dy) + self._nudge[1],
            )
            return {"RUNNING_MODAL"}

        if event.type in _NUDGES and event.value == "PRESS":
            dx, dy = _NUDGES[event.type]
            self._nudge[0] += dx
            self._nudge[1] += dy
            layer.text_origin = (layer.text_origin[0] + dx, layer.text_origin[1] + dy)
            return {"RUNNING_MODAL"}

        if transform._confirm_event(event, self.drag):
            if (layer.text_origin[0], layer.text_origin[1]) == self._origin:
                return {"CANCELLED"}
            with layers._undo_step(context, canvas, "Blix Move Text"):
                pass
            return {"FINISHED"}

        if event.type in {"ESC", "RIGHTMOUSE"}:
            layer.text_origin = self._origin
            undo.forget(canvas)
            return {"CANCELLED"}

        return {"RUNNING_MODAL"}


class BLIX_OT_text_rasterize(bpy.types.Operator):
    """Turn the active text layer into a plain pixel layer"""

    bl_idname = "blix.text_rasterize"
    bl_label = "Rasterize Text"
    bl_options = {"REGISTER", "INTERNAL"}

    @classmethod
    def poll(cls, context: bpy.types.Context) -> bool:
        return active_text(context) is not None

    def execute(self, context: bpy.types.Context) -> set[OperatorReturnItems]:
        found = active_text(context)
        assert found is not None
        canvas, layer = found
        with layers._undo_step(context, canvas, "Blix Rasterize Text"):
            layer.is_text = False
        overlay.tag_redraw(context)
        return {"FINISHED"}


class BLIX_TOOL_text(bpy.types.WorkSpaceTool):
    bl_space_type = "IMAGE_EDITOR"
    bl_context_mode = "PAINT"
    bl_idname = TOOL_ID
    bl_label = "Blix Text"
    bl_description = "Click to add a text layer, or drag the active text layer to move it"
    bl_icon = "ops.gpencil.draw.poly"
    bl_widget = None
    bl_keymap = (
        ("blix.text_add", {"type": "LEFTMOUSE", "value": "PRESS"}, None),
        ("blix.text_move", {"type": "G", "value": "PRESS"}, None),
    )


_classes = (
    BLIX_OT_text_add,
    BLIX_OT_text_move,
    BLIX_OT_text_rasterize,
)


def register() -> None:
    for cls in _classes:
        bpy.utils.register_class(cls)
    bpy.utils.register_tool(BLIX_TOOL_text, separator=True)
    overlay.extra_draws.append(_draw)
    bpy.app.handlers.undo_post.append(_undo_refresh)
    bpy.app.handlers.redo_post.append(_undo_refresh)


def unregister() -> None:
    bpy.app.handlers.redo_post.remove(_undo_refresh)
    bpy.app.handlers.undo_post.remove(_undo_refresh)
    overlay.extra_draws.remove(_draw)
    bpy.utils.unregister_tool(BLIX_TOOL_text)
    for path in _fonts:
        blf.unload(path)
    _fonts.clear()
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
