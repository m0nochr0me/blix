"""Reference layers: images and movie frames drawn over or under the canvas at their own scale."""

import math
from typing import TYPE_CHECKING, Any, cast

import bpy
import gpu
import numpy as np
from bpy.app.handlers import persistent
from bpy_extras.io_utils import ImportHelper

from . import overlay, prefs, props, select, transform

if TYPE_CHECKING:
    from bpy.stub_internal.rna_enums import OperatorReturnItems

TOOL_ID = "blix.reference"
_MODES = (
    ("MOVE", "Move", ""),
    ("ROTATE", "Rotate", ""),
    ("SCALE", "Scale", ""),
)
_NUDGES = {
    "LEFT_ARROW": (-1, 0),
    "RIGHT_ARROW": (1, 0),
    "UP_ARROW": (0, -1),
    "DOWN_ARROW": (0, 1),
}

_loaded: dict[str, int] = {}


def _redraw(_self: Any, context: bpy.types.Context) -> None:
    overlay.tag_redraw(context)


class BlixReference(bpy.types.PropertyGroup):
    image: bpy.props.PointerProperty(type=bpy.types.Image, update=_redraw)
    offset: bpy.props.FloatVectorProperty(
        name="Offset",
        description="Center in canvas pixels from the top-left corner",
        size=2,
        subtype="XYZ",
        update=_redraw,
    )
    scale: bpy.props.FloatProperty(
        name="Scale",
        description="Canvas pixels per reference pixel",
        default=1.0,
        min=0.001,
        soft_max=16.0,
        precision=3,
        update=_redraw,
    )
    rotation: bpy.props.FloatProperty(
        name="Rotation", subtype="ANGLE", default=0.0, update=_redraw
    )
    flip_x: bpy.props.BoolProperty(name="Flip X", default=False, update=_redraw)
    flip_y: bpy.props.BoolProperty(name="Flip Y", default=False, update=_redraw)
    opacity: bpy.props.FloatProperty(
        name="Opacity", default=0.5, min=0.0, max=1.0, update=_redraw
    )
    visible: bpy.props.BoolProperty(name="Visible", default=True, update=_redraw)
    behind: bpy.props.BoolProperty(
        name="Behind",
        description="Draw under the painting instead of over it",
        default=False,
        update=_redraw,
    )
    follow_scene: bpy.props.BoolProperty(
        name="Follow Timeline",
        description="Show the movie frame at the scene frame plus the frame offset",
        default=False,
        update=_redraw,
    )
    frame: bpy.props.IntProperty(name="Frame", default=1, min=1, update=_redraw)
    frame_offset: bpy.props.IntProperty(name="Frame Offset", default=0, update=_redraw)


def owner(image: bpy.types.Image) -> bpy.types.Image:
    """Image holding the references: the canvas for a layer image, else the image itself."""
    return props.canvas_of(image) or image


def active(canvas: bpy.types.Image) -> Any:
    stack = props.references(canvas)
    index = props.references_index(canvas)
    if 0 <= index < len(stack):
        return stack[index]
    return None


def add(canvas: bpy.types.Image, image: bpy.types.Image) -> Any:
    """Append a reference centered on the canvas, shrunk to fit when larger than it."""
    width, height = canvas.size
    ref_width, ref_height = image.size
    reference = props.references(canvas).add()
    reference.image = image
    reference.offset = (width / 2, height / 2)
    reference.scale = min(1.0, width / max(ref_width, 1), height / max(ref_height, 1))
    props.set_references_index(canvas, len(props.references(canvas)) - 1)
    return reference


def frame_of(reference: Any, scene: bpy.types.Scene) -> int | None:
    """Movie frame to show, clamped to the movie length; None for a still image."""
    image = reference.image
    if image is None or image.source != "MOVIE":
        return None
    if reference.follow_scene:
        frame = scene.frame_current + reference.frame_offset
    else:
        frame = reference.frame
    return max(1, min(frame, max(image.frame_duration, 1)))


def corners(reference: Any, canvas_height: int) -> select.Quad:
    """Reference corners in canvas pixels (y up) in texture UV order, flips and rotation applied."""
    width, height = reference.image.size
    fx = -1.0 if reference.flip_x else 1.0
    fy = -1.0 if reference.flip_y else 1.0
    cos_r, sin_r = math.cos(reference.rotation), math.sin(reference.rotation)
    cx, cy = reference.offset[0], canvas_height - reference.offset[1]
    quad: select.Quad = []
    for x, y in ((0.0, 0.0), (width, 0.0), (width, height), (0.0, height)):
        lx = (x - width / 2) * fx * reference.scale
        ly = (y - height / 2) * fy * reference.scale
        quad.append((cx + lx * cos_r - ly * sin_r, cy + lx * sin_r + ly * cos_r))
    return quad


def _texture(reference: Any, scene: bpy.types.Scene) -> gpu.types.GPUTexture:
    """GPU texture of the reference; movies reload their GPU texture when the frame changes."""
    image = reference.image
    frame = frame_of(reference, scene)
    if frame is not None and _loaded.get(image.name) != frame:
        image.gl_free()
        image.gl_load(frame=frame)
        _loaded[image.name] = frame
    return gpu.texture.from_image(image)


def _tool_active(context: bpy.types.Context) -> bool:
    workspace = context.workspace
    if workspace is None:
        return False
    tool = workspace.tools.from_space_image_mode("PAINT", create=False)
    return tool is not None and tool.idname == TOOL_ID


def _region_quad(affine: select.Affine, quad: select.Quad) -> select.Quad:
    return select._segment_points(affine, np.asarray(quad, dtype=np.float32))


def _shown(canvas: bpy.types.Image, behind: bool) -> list[Any]:
    return [
        reference
        for reference in props.references(canvas)
        if reference.visible
        and reference.behind == behind
        and reference.image is not None
        and reference.image.size[0] > 0
    ]


def _scissor_to(quad: select.Quad, saved: tuple[int, int, int, int]) -> bool:
    """Clip drawing to the quad's region-space bounds within the saved scissor; False if empty."""
    xs = [x for x, _ in quad]
    ys = [y for _, y in quad]
    x0 = max(math.floor(min(xs)), saved[0])
    y0 = max(math.floor(min(ys)), saved[1])
    x1 = min(math.ceil(max(xs)), saved[0] + saved[2])
    y1 = min(math.ceil(max(ys)), saved[1] + saved[3])
    if x1 <= x0 or y1 <= y0:
        return False
    gpu.state.scissor_test_set(True)
    gpu.state.scissor_set(x0, y0, x1 - x0, y1 - y0)
    return True


def _draw_set(region: bpy.types.Region, image: bpy.types.Image, behind: bool) -> None:
    canvas = owner(image)
    shown = _shown(canvas, behind)
    outline = not behind and _tool_active(bpy.context)
    if not shown and not outline:
        return
    affine = select._image_affine(region, image)
    scene = bpy.context.scene
    if affine is None or scene is None:
        return
    width, height = image.size
    canvas_quad = _region_quad(affine, [(0.0, 0.0), (width, 0.0), (width, height), (0.0, height)])
    saved = gpu.state.scissor_get()
    if shown and _scissor_to(canvas_quad, saved):
        for reference in shown:
            quad = _region_quad(affine, corners(reference, height))
            select.draw_texture_quad(quad, _texture(reference, scene), False, reference.opacity)
        if behind:
            select.draw_texture_quad(canvas_quad, gpu.texture.from_image(image), False)
        gpu.state.scissor_set(*saved)
    if not outline:
        return
    reference = active(canvas)
    if reference is None or reference.image is None or reference.image.size[0] == 0:
        return
    quad = _region_quad(affine, corners(reference, height))
    overlay.draw_lines(select._loop_points(quad), prefs.color("outline_color"))


def _draw_behind(region: bpy.types.Region, image: bpy.types.Image) -> None:
    _draw_set(region, image, True)


def _draw_front(region: bpy.types.Region, image: bpy.types.Image) -> None:
    _draw_set(region, image, False)


@persistent
def _clear_loaded(*_args: Any) -> None:
    _loaded.clear()


def _filter_glob() -> str:
    path = cast(Any, bpy.path)
    extensions = path.extensions_image | path.extensions_movie
    return ";".join(f"*{extension}" for extension in sorted(extensions))


class BLIX_OT_reference_import(bpy.types.Operator, ImportHelper):
    """Load an image or movie file as a reference layer of the current image"""

    bl_idname = "blix.reference_import"
    bl_label = "Import Reference"
    bl_options = {"REGISTER", "UNDO"}

    filter_glob: bpy.props.StringProperty(default=_filter_glob(), options={"HIDDEN"})

    @classmethod
    def poll(cls, context: bpy.types.Context) -> bool:
        return select.edit_image(context) is not None

    def execute(self, context: bpy.types.Context) -> set[OperatorReturnItems]:
        image = select.edit_image(context)
        assert image is not None
        filepath = cast(Any, self).filepath
        try:
            loaded = bpy.data.images.load(filepath, check_existing=False)
        except RuntimeError as error:
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}
        if loaded.size[0] == 0:
            bpy.data.images.remove(loaded)
            self.report({"ERROR"}, f"Cannot read {filepath}")
            return {"CANCELLED"}
        add(owner(image), loaded)
        overlay.tag_redraw(context)
        return {"FINISHED"}


class BLIX_OT_reference_remove(bpy.types.Operator):
    """Remove the active reference layer"""

    bl_idname = "blix.reference_remove"
    bl_label = "Remove Reference"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context: bpy.types.Context) -> bool:
        image = select.edit_image(context)
        return image is not None and len(props.references(owner(image))) > 0

    def execute(self, context: bpy.types.Context) -> set[OperatorReturnItems]:
        image = select.edit_image(context)
        assert image is not None
        canvas = owner(image)
        stack = props.references(canvas)
        index = min(props.references_index(canvas), len(stack) - 1)
        stack.remove(index)
        props.set_references_index(canvas, min(index, len(stack) - 1))
        overlay.tag_redraw(context)
        return {"FINISHED"}


class BLIX_OT_reference_transform(bpy.types.Operator):
    """Move, rotate or scale the active reference; Ctrl locks the axis or snaps the angle"""

    bl_idname = "blix.reference_transform"
    bl_label = "Transform Reference"
    bl_options = {"REGISTER", "UNDO", "INTERNAL"}

    mode: bpy.props.EnumProperty(items=_MODES, default="MOVE", options={"SKIP_SAVE", "HIDDEN"})
    drag: bpy.props.BoolProperty(default=False, options={"SKIP_SAVE", "HIDDEN"})

    _start: tuple[float, float]
    _center: tuple[float, float]
    _offset: tuple[float, float]
    _scale: float
    _rotation: float
    _nudge: list[int]

    @classmethod
    def poll(cls, context: bpy.types.Context) -> bool:
        reference = cls._reference(context)
        return reference is not None and reference.image is not None

    @staticmethod
    def _reference(context: bpy.types.Context) -> Any:
        image = select.edit_image(context)
        if image is None:
            return None
        return active(owner(image))

    def invoke(
        self, context: bpy.types.Context, event: bpy.types.Event
    ) -> set[OperatorReturnItems]:
        image = select.edit_image(context)
        region = context.region
        reference = self._reference(context)
        assert image is not None and region is not None and reference is not None
        self._offset = (reference.offset[0], reference.offset[1])
        self._scale = reference.scale
        self._rotation = reference.rotation
        self._start = select.mouse_pixel(region, image, event)
        self._center = (self._offset[0], image.size[1] - self._offset[1])
        self._nudge = [0, 0]
        window_manager = context.window_manager
        assert window_manager is not None
        window_manager.modal_handler_add(self)
        return {"RUNNING_MODAL"}

    def _apply(self, reference: Any, mouse: tuple[float, float], ctrl: bool) -> None:
        if self.mode == "MOVE":
            dx, dy = mouse[0] - self._start[0], mouse[1] - self._start[1]
            if ctrl:
                if abs(dx) >= abs(dy):
                    dy = 0.0
                else:
                    dx = 0.0
            reference.offset = (
                round(self._offset[0] + dx) + self._nudge[0],
                round(self._offset[1] - dy) + self._nudge[1],
            )
            return
        cx, cy = self._center
        if self.mode == "ROTATE":
            turned = math.atan2(mouse[1] - cy, mouse[0] - cx)
            started = math.atan2(self._start[1] - cy, self._start[0] - cx)
            angle = self._rotation + turned - started
            if ctrl:
                angle = round(angle / transform.SNAP_ANGLE) * transform.SNAP_ANGLE
            reference.rotation = angle
            return
        start = max(math.hypot(self._start[0] - cx, self._start[1] - cy), 1e-3)
        reference.scale = self._scale * math.hypot(mouse[0] - cx, mouse[1] - cy) / start

    def modal(self, context: bpy.types.Context, event: bpy.types.Event) -> set[OperatorReturnItems]:
        image = select.edit_image(context)
        region = context.region
        reference = self._reference(context)
        if image is None or region is None or reference is None:
            return {"CANCELLED"}

        if event.type == "MOUSEMOVE":
            self._apply(reference, select.mouse_pixel(region, image, event), event.ctrl)
            overlay.tag_redraw(context)
            return {"RUNNING_MODAL"}

        if self.mode == "MOVE" and event.type in _NUDGES and event.value == "PRESS":
            dx, dy = _NUDGES[event.type]
            self._nudge[0] += dx
            self._nudge[1] += dy
            reference.offset = (reference.offset[0] + dx, reference.offset[1] + dy)
            overlay.tag_redraw(context)
            return {"RUNNING_MODAL"}

        if transform._confirm_event(event, self.drag):
            return {"FINISHED"}

        if event.type in {"ESC", "RIGHTMOUSE"}:
            reference.offset = self._offset
            reference.scale = self._scale
            reference.rotation = self._rotation
            overlay.tag_redraw(context)
            return {"CANCELLED"}

        return {"RUNNING_MODAL"}


class BLIX_TOOL_reference(bpy.types.WorkSpaceTool):
    bl_space_type = "IMAGE_EDITOR"
    bl_context_mode = "PAINT"
    bl_idname = TOOL_ID
    bl_label = "Blix Reference"
    bl_description = "Move, rotate and scale the active reference layer"
    bl_icon = "ops.transform.transform"
    bl_widget = None
    bl_keymap = (
        (
            "blix.reference_transform",
            {"type": "LEFTMOUSE", "value": "PRESS"},
            {"properties": [("mode", "MOVE"), ("drag", True)]},
        ),
        (
            "blix.reference_transform",
            {"type": "G", "value": "PRESS"},
            {"properties": [("mode", "MOVE")]},
        ),
        (
            "blix.reference_transform",
            {"type": "R", "value": "PRESS"},
            {"properties": [("mode", "ROTATE")]},
        ),
        (
            "blix.reference_transform",
            {"type": "S", "value": "PRESS"},
            {"properties": [("mode", "SCALE")]},
        ),
    )


_classes = (
    BlixReference,
    BLIX_OT_reference_import,
    BLIX_OT_reference_remove,
    BLIX_OT_reference_transform,
)


def register() -> None:
    for cls in _classes:
        bpy.utils.register_class(cls)
    image_cls = cast(Any, bpy.types.Image)
    image_cls.blix_references = bpy.props.CollectionProperty(type=BlixReference)
    image_cls.blix_references_index = bpy.props.IntProperty(default=0, update=_redraw)
    bpy.utils.register_tool(BLIX_TOOL_reference, separator=True)
    overlay.under_draws.insert(0, _draw_behind)
    overlay.under_draws.append(_draw_front)
    bpy.app.handlers.load_post.append(_clear_loaded)


def unregister() -> None:
    bpy.app.handlers.load_post.remove(_clear_loaded)
    overlay.under_draws.remove(_draw_front)
    overlay.under_draws.remove(_draw_behind)
    bpy.utils.unregister_tool(BLIX_TOOL_reference)
    _loaded.clear()
    image_cls = cast(Any, bpy.types.Image)
    del image_cls.blix_references_index
    del image_cls.blix_references
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
