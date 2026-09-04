"""Guide data model on Image and interactive guide operators."""

import math
from typing import TYPE_CHECKING, Any, cast

import bpy

from . import overlay, props

if TYPE_CHECKING:
    from bpy.stub_internal.rna_enums import OperatorReturnItems

GRAB_RADIUS = 6.0


class BlixGuide(bpy.types.PropertyGroup):
    orientation: bpy.props.EnumProperty(
        name="Orientation",
        items=(
            ("VERTICAL", "Vertical", "Guide along Y at X position"),
            ("HORIZONTAL", "Horizontal", "Guide along X at Y position"),
            ("DIAGONAL", "Diagonal", "Guide through X, Y at angle"),
        ),
        default="VERTICAL",
    )
    position: bpy.props.IntProperty(name="Position", default=0)
    position_y: bpy.props.IntProperty(name="Y", default=0)
    angle: bpy.props.FloatProperty(name="Angle", subtype="ANGLE", default=math.pi / 4, step=100)


def _image(context: bpy.types.Context) -> bpy.types.Image | None:
    space = context.space_data
    if space is None or space.type != "IMAGE_EDITOR":
        return None
    return cast(bpy.types.SpaceImageEditor, space).image


def find_near(region: bpy.types.Region, image: bpy.types.Image, mx: float, my: float) -> int:
    best = -1
    best_dist = GRAB_RADIUS * overlay.ui_scale()
    for index, guide in enumerate(props.guides(image)):
        if guide.orientation == "VERTICAL":
            gx, _ = overlay.image_to_region(region, image, guide.position, 0.0)
            dist = abs(mx - gx)
        elif guide.orientation == "HORIZONTAL":
            _, gy = overlay.image_to_region(region, image, 0.0, image.size[1] - guide.position)
            dist = abs(my - gy)
        else:
            gx, gy = overlay.image_to_region(
                region, image, guide.position, image.size[1] - guide.position_y
            )
            dist = abs((mx - gx) * math.sin(guide.angle) - (my - gy) * math.cos(guide.angle))
        if dist <= best_dist:
            best_dist = dist
            best = index
    return best


def outside(image: bpy.types.Image, guide: Any) -> bool:
    width, height = image.size
    if guide.orientation == "DIAGONAL":
        segment = overlay.clip_line(
            guide.position,
            height - guide.position_y,
            math.cos(guide.angle),
            math.sin(guide.angle),
            width,
            height,
        )
        return segment is None
    extent = height if guide.orientation == "HORIZONTAL" else width
    return guide.position < 0 or guide.position > extent


class BLIX_OT_guide_add(bpy.types.Operator):
    """Add guide at image center"""

    bl_idname = "blix.guide_add"
    bl_label = "Add Guide"
    bl_options = {"REGISTER", "UNDO"}

    orientation: bpy.props.EnumProperty(
        name="Orientation",
        items=(
            ("VERTICAL", "Vertical", ""),
            ("HORIZONTAL", "Horizontal", ""),
            ("DIAGONAL", "Diagonal", ""),
        ),
        default="VERTICAL",
    )

    @classmethod
    def poll(cls, context: bpy.types.Context) -> bool:
        return _image(context) is not None

    def execute(self, context: bpy.types.Context) -> set[OperatorReturnItems]:
        image = _image(context)
        assert image is not None
        guide = props.guides(image).add()
        guide.orientation = self.orientation
        extent = image.size[1] if self.orientation == "HORIZONTAL" else image.size[0]
        guide.position = extent // 2
        guide.position_y = image.size[1] // 2
        props.set_guides_index(image, len(props.guides(image)) - 1)
        overlay.tag_redraw(context)
        return {"FINISHED"}


class BLIX_OT_guide_remove(bpy.types.Operator):
    """Remove active guide"""

    bl_idname = "blix.guide_remove"
    bl_label = "Remove Guide"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context: bpy.types.Context) -> bool:
        image = _image(context)
        return image is not None and len(props.guides(image)) > 0

    def execute(self, context: bpy.types.Context) -> set[OperatorReturnItems]:
        image = _image(context)
        assert image is not None
        index = min(props.guides_index(image), len(props.guides(image)) - 1)
        props.guides(image).remove(index)
        props.set_guides_index(image, min(index, len(props.guides(image)) - 1))
        overlay.tag_redraw(context)
        return {"FINISHED"}


class BLIX_OT_guide_clear(bpy.types.Operator):
    """Remove all guides"""

    bl_idname = "blix.guide_clear"
    bl_label = "Clear Guides"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context: bpy.types.Context) -> bool:
        image = _image(context)
        return image is not None and len(props.guides(image)) > 0

    def execute(self, context: bpy.types.Context) -> set[OperatorReturnItems]:
        image = _image(context)
        assert image is not None
        props.guides(image).clear()
        props.set_guides_index(image, 0)
        overlay.tag_redraw(context)
        return {"FINISHED"}


class BLIX_OT_guide_drag(bpy.types.Operator):
    """Drag guide: create from ruler band or corner, Ctrl grabs nearby guide"""

    bl_idname = "blix.guide_drag"
    bl_label = "Drag Guide"
    bl_options = {"REGISTER", "UNDO", "INTERNAL"}

    _index: int
    _is_new: bool
    _original: tuple[int, int]

    def invoke(
        self, context: bpy.types.Context, event: bpy.types.Event
    ) -> set[OperatorReturnItems]:
        image = _image(context)
        if image is None or image.size[0] == 0 or image.size[1] == 0:
            return {"PASS_THROUGH"}
        region = context.region
        scene = context.scene
        if region is None or region.type != "WINDOW":
            return {"PASS_THROUGH"}
        mx, my = event.mouse_region_x, event.mouse_region_y
        band = overlay.ruler_px()
        left_off, top_off = overlay.ruler_offsets(context.area, region)
        rulers = scene is not None and props.show_rulers(scene)
        in_top = my >= region.height - top_off - band
        in_left = mx <= left_off + band

        if event.ctrl:
            index = find_near(region, image, mx, my)
            if index < 0:
                return {"PASS_THROUGH"}
            guide = props.guides(image)[index]
            self._index = index
            self._is_new = False
            self._original = (guide.position, guide.position_y)
        elif rulers and in_top and in_left:
            self._start(image, "DIAGONAL")
        elif rulers and in_top:
            self._start(image, "HORIZONTAL")
        elif rulers and in_left:
            self._start(image, "VERTICAL")
        else:
            return {"PASS_THROUGH"}

        window_manager = context.window_manager
        assert window_manager is not None
        window_manager.modal_handler_add(self)
        return {"RUNNING_MODAL"}

    def _start(self, image: bpy.types.Image, orientation: str) -> None:
        guide = props.guides(image).add()
        guide.orientation = orientation
        self._index = len(props.guides(image)) - 1
        self._is_new = True
        self._original = (0, 0)

    def modal(self, context: bpy.types.Context, event: bpy.types.Event) -> set[OperatorReturnItems]:
        image = _image(context)
        if image is None:
            return {"CANCELLED"}
        region = context.region
        assert region is not None
        guide = props.guides(image)[self._index]

        if event.type == "MOUSEMOVE":
            x, y = overlay.region_to_image(
                region, image, event.mouse_region_x, event.mouse_region_y
            )
            if guide.orientation == "HORIZONTAL":
                guide.position = round(image.size[1] - y)
            else:
                guide.position = round(x)
                guide.position_y = round(image.size[1] - y)
            overlay.tag_redraw(context)
            return {"RUNNING_MODAL"}

        if event.type == "LEFTMOUSE" and event.value == "RELEASE":
            if outside(image, guide):
                props.guides(image).remove(self._index)
            overlay.tag_redraw(context)
            return {"FINISHED"}

        if event.type in {"ESC", "RIGHTMOUSE"}:
            if self._is_new:
                props.guides(image).remove(self._index)
            else:
                guide.position, guide.position_y = self._original
            overlay.tag_redraw(context)
            return {"CANCELLED"}

        return {"RUNNING_MODAL"}


_classes = (
    BlixGuide,
    BLIX_OT_guide_add,
    BLIX_OT_guide_remove,
    BLIX_OT_guide_clear,
    BLIX_OT_guide_drag,
)

_keymaps: list[tuple[bpy.types.KeyMap, bpy.types.KeyMapItem]] = []


def _register_keymap() -> None:
    window_manager = bpy.context.window_manager
    if window_manager is None:
        return
    keyconfig = window_manager.keyconfigs.addon
    if keyconfig is None:
        return
    keymap = keyconfig.keymaps.new(name="Screen Editing", space_type="EMPTY")
    for ctrl in (False, True):
        item = keymap.keymap_items.new(
            BLIX_OT_guide_drag.bl_idname, "LEFTMOUSE", "PRESS", ctrl=ctrl
        )
        _keymaps.append((keymap, item))


def _unregister_keymap() -> None:
    for keymap, item in _keymaps:
        keymap.keymap_items.remove(item)
    _keymaps.clear()


def register() -> None:
    for cls in _classes:
        bpy.utils.register_class(cls)
    image_cls = cast(Any, bpy.types.Image)
    image_cls.blix_guides = bpy.props.CollectionProperty(type=BlixGuide)
    image_cls.blix_guides_index = bpy.props.IntProperty(default=0)
    _register_keymap()


def unregister() -> None:
    _unregister_keymap()
    image_cls = cast(Any, bpy.types.Image)
    del image_cls.blix_guides_index
    del image_cls.blix_guides
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
