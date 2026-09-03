"""Cel animation: layers as frames, ranges keyed as Scene-hosted visibility tracks."""

import re
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import bpy
import numpy as np
from bpy.app.handlers import persistent
from bpy_extras.io_utils import ExportHelper

from . import overlay, props

if TYPE_CHECKING:
    from bpy.stub_internal.rna_enums import OperatorReturnItems

_PATH = re.compile(r"blix_cels\[(\d+)\](.*)")

MODE_ITEMS = (
    ("STRIP", "Strip", "All frames side by side in one image, first frame left"),
    ("SEQUENCE", "PNG Sequence", "One numbered file per frame"),
)


def track(scene: bpy.types.Scene, canvas: bpy.types.Image, layer: Any) -> Any:
    for item in props.cels(scene):
        if item.canvas == canvas and item.number == layer.number and item.copy == layer.copy:
            return item
    return None


def animated(scene: bpy.types.Scene, canvas: bpy.types.Image) -> bool:
    return any(item.canvas == canvas for item in props.cels(scene))


def on(canvas: bpy.types.Image, layer: Any) -> bool:
    """Animated visibility of layer at the current frame; True when the canvas has no tracks."""
    scene = bpy.context.scene
    if scene is None:
        return True
    item = track(scene, canvas, layer)
    return True if item is None else bool(item.visible)


def _channelbag(scene: bpy.types.Scene) -> Any:
    data = scene.animation_data
    if data is None or data.action is None or data.action_slot is None:
        return None
    action_layers = data.action.layers
    if not len(action_layers) or not len(action_layers[0].strips):
        return None
    return cast(Any, action_layers[0].strips[0]).channelbag(data.action_slot)


def _fcurve(scene: bpy.types.Scene, item: Any) -> bpy.types.FCurve | None:
    bag = _channelbag(scene)
    if bag is None:
        return None
    return bag.fcurves.find(item.path_from_id("visible"))


def _add_track(scene: bpy.types.Scene, canvas: bpy.types.Image, layer: Any) -> Any:
    from . import layers

    item = props.cels(scene).add()
    item.name = f"{canvas.name}/{layers.layer_tag(layer)}"
    item.canvas = canvas
    item.number = layer.number
    item.copy = layer.copy
    return item


def _remove_track(scene: bpy.types.Scene, index: int) -> None:
    """Drop a track with its curves, then re-index curve paths of the tracks after it."""
    bag = _channelbag(scene)
    if bag is not None:
        prefix = f"blix_cels[{index}]."
        for curve in [curve for curve in bag.fcurves if curve.data_path.startswith(prefix)]:
            bag.fcurves.remove(curve)
        for curve in bag.fcurves:
            match = _PATH.match(curve.data_path)
            if match is not None and int(match.group(1)) > index:
                curve.data_path = f"blix_cels[{int(match.group(1)) - 1}]{match.group(2)}"
    props.cels(scene).remove(index)


def sync(scene: bpy.types.Scene, canvas: bpy.types.Image) -> None:
    """Keep one track per layer of an animated canvas: add missing ones, drop orphans."""
    if not animated(scene, canvas):
        return
    stack = list(props.layers(canvas))
    keys = {(layer.number, layer.copy) for layer in stack}
    items = props.cels(scene)
    for index in reversed(range(len(items))):
        item = items[index]
        if item.canvas == canvas and (item.number, item.copy) not in keys:
            _remove_track(scene, index)
    for layer in stack:
        if track(scene, canvas, layer) is None:
            _add_track(scene, canvas, layer)


def enable(scene: bpy.types.Scene, canvas: bpy.types.Image) -> None:
    for layer in props.layers(canvas):
        if track(scene, canvas, layer) is None:
            _add_track(scene, canvas, layer)


def disable(scene: bpy.types.Scene, canvas: bpy.types.Image) -> None:
    items = props.cels(scene)
    for index in reversed(range(len(items))):
        if items[index].canvas == canvas:
            _remove_track(scene, index)


def frame_range(scene: bpy.types.Scene, item: Any) -> tuple[int, int]:
    """First keyed on-range of the track; the scene range when unkeyed."""
    curve = _fcurve(scene, item)
    if curve is None:
        return scene.frame_start, scene.frame_end
    points = curve.keyframe_points
    for index, point in enumerate(points):
        if point.co.y < 0.5:
            continue
        first = round(point.co.x)
        later = [round(other.co.x) for other in points[index + 1 :] if other.co.y < 0.5]
        return first, later[0] - 1 if later else scene.frame_end
    return scene.frame_start, scene.frame_start - 1


def set_range(scene: bpy.types.Scene, item: Any, first: int, last: int) -> None:
    """Key the track on from first to last inclusive and off around it, replacing old keys."""
    curve = _fcurve(scene, item)
    if curve is not None:
        _channelbag(scene).fcurves.remove(curve)
    path = item.path_from_id("visible")
    for frame, value in ((first - 1, False), (first, True), (last + 1, False)):
        item.visible = value
        scene.keyframe_insert(path, frame=frame)
    item.visible = first <= scene.frame_current <= last


def _shift(scene: bpy.types.Scene, canvas: bpy.types.Image, after: int, delta: int) -> None:
    """Move keys past `after` by delta; off keys at after+1 stay so cels ending there keep it."""
    bag = _channelbag(scene)
    if bag is None:
        return
    for index, item in enumerate(props.cels(scene)):
        if item.canvas != canvas:
            continue
        curve = bag.fcurves.find(f"blix_cels[{index}].visible")
        if curve is None:
            continue
        for point in curve.keyframe_points:
            ends_here = point.co.x == after + 1 and point.co.y < 0.5
            if point.co.x > after and not ends_here:
                point.co.x += delta
                point.handle_left.x += delta
                point.handle_right.x += delta
        curve.update()


def _retarget_editors(canvas: bpy.types.Image, image: bpy.types.Image) -> None:
    for manager in bpy.data.window_managers:
        for window in manager.windows:
            for area in window.screen.areas:
                if area.type != "IMAGE_EDITOR":
                    continue
                space = cast(bpy.types.SpaceImageEditor, area.spaces.active)
                shown = space.image
                if shown is None or shown in (canvas, image):
                    continue
                if props.canvas_of(shown) == canvas:
                    space.image = image


def _follow(canvas: bpy.types.Image) -> None:
    from . import layers

    stack = props.layers(canvas)
    index = next(
        (
            position
            for position, layer in enumerate(stack)
            if layer.cel and layer.image is not None and layers.shown(canvas, layer)
        ),
        None,
    )
    if index is None:
        return
    if index != props.layers_index(canvas):
        props.set_layers_index(canvas, index)
    _retarget_editors(canvas, stack[index].image)


def _apply(scene: bpy.types.Scene, canvas: bpy.types.Image) -> None:
    from . import layers

    layers.sync_canvas(canvas)
    if props.cel_follow(scene):
        _follow(canvas)
    layers._heal(canvas)


def refresh(scene: bpy.types.Scene) -> None:
    """Recomposite every animated canvas of the scene for the current frame, no undo step."""
    canvases = {
        item.canvas.name: item.canvas for item in props.cels(scene) if item.canvas is not None
    }
    for canvas in canvases.values():
        if len(props.layers(canvas)):
            _apply(scene, canvas)
    if canvases:
        overlay.tag_redraw(bpy.context)


@persistent
def _frame_changed(scene: bpy.types.Scene, *_args: Any) -> None:
    refresh(scene)


def _visible_changed(self: bpy.types.PropertyGroup, context: bpy.types.Context) -> None:
    scene = cast(bpy.types.Scene, self.id_data)
    canvas = cast(Any, self).canvas
    if canvas is not None and len(props.layers(canvas)):
        _apply(scene, canvas)
        overlay.tag_redraw(context)


def _get_start(self: bpy.types.PropertyGroup) -> int:
    return frame_range(cast(bpy.types.Scene, self.id_data), self)[0]


def _set_start(self: bpy.types.PropertyGroup, value: int) -> None:
    scene = cast(bpy.types.Scene, self.id_data)
    _first, last = frame_range(scene, self)
    set_range(scene, self, value, max(last, value))
    refresh(scene)


def _get_end(self: bpy.types.PropertyGroup) -> int:
    return frame_range(cast(bpy.types.Scene, self.id_data), self)[1]


def _set_end(self: bpy.types.PropertyGroup, value: int) -> None:
    scene = cast(bpy.types.Scene, self.id_data)
    first, _last = frame_range(scene, self)
    set_range(scene, self, min(first, value), value)
    refresh(scene)


class BlixCel(bpy.types.PropertyGroup):
    canvas: bpy.props.PointerProperty(type=bpy.types.Image)
    number: bpy.props.IntProperty(name="Number", min=0)
    copy: bpy.props.IntProperty(name="Copy", min=0)
    visible: bpy.props.BoolProperty(name="Visible", default=True, update=_visible_changed)
    frame_start: bpy.props.IntProperty(
        name="Start",
        description="First frame the layer is shown on",
        options=set(),
        get=_get_start,
        set=_set_start,
    )
    frame_end: bpy.props.IntProperty(
        name="End",
        description="Last frame the layer is shown on",
        options=set(),
        get=_get_end,
        set=_set_end,
    )


class _CelOperator(bpy.types.Operator):
    @classmethod
    def poll(cls, context: bpy.types.Context) -> bool:
        from . import layers

        canvas = layers.resolve_canvas(context)
        scene = context.scene
        return canvas is not None and scene is not None and animated(scene, canvas)


class BLIX_OT_cels_enable(bpy.types.Operator):
    """Animate the layers of this canvas in the current scene, one visibility track per layer"""

    bl_idname = "blix.cels_enable"
    bl_label = "Animate Layers"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context: bpy.types.Context) -> bool:
        from . import layers

        canvas = layers.resolve_canvas(context)
        scene = context.scene
        return canvas is not None and scene is not None and not animated(scene, canvas)

    def execute(self, context: bpy.types.Context) -> set[OperatorReturnItems]:
        from . import layers

        canvas = layers.resolve_canvas(context)
        scene = context.scene
        assert canvas is not None and scene is not None
        enable(scene, canvas)
        return {"FINISHED"}


class BLIX_OT_cels_disable(_CelOperator):
    """Remove the layer animation tracks of this canvas from the current scene"""

    bl_idname = "blix.cels_disable"
    bl_label = "Stop Animating"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context: bpy.types.Context) -> set[OperatorReturnItems]:
        from . import layers

        canvas = layers.resolve_canvas(context)
        scene = context.scene
        assert canvas is not None and scene is not None
        disable(scene, canvas)
        layers._heal(canvas)
        overlay.tag_redraw(context)
        return {"FINISHED"}


class BLIX_OT_cels_layout(_CelOperator):
    """Give every cel layer a consecutive frame range and fit the scene range to them"""

    bl_idname = "blix.cels_layout"
    bl_label = "Layout Cels"
    bl_options = {"REGISTER", "UNDO"}

    start: bpy.props.IntProperty(name="Start Frame", default=1)
    hold: bpy.props.IntProperty(name="Frames per Cel", default=1, min=1)
    top_first: bpy.props.BoolProperty(
        name="Top Layer First", description="Order cels from the top of the stack", default=False
    )

    def invoke(
        self, context: bpy.types.Context, event: bpy.types.Event
    ) -> set[OperatorReturnItems]:
        assert context.scene is not None and context.window_manager is not None
        self.start = context.scene.frame_start
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context: bpy.types.Context) -> set[OperatorReturnItems]:
        from . import layers

        canvas = layers.resolve_canvas(context)
        scene = context.scene
        assert canvas is not None and scene is not None
        cel_layers = [layer for layer in props.layers(canvas) if layer.cel]
        if not cel_layers:
            self.report({"ERROR"}, "No cel layers")
            return {"CANCELLED"}
        if not self.top_first:
            cel_layers.reverse()
        sync(scene, canvas)
        frame = self.start
        for layer in cel_layers:
            set_range(scene, track(scene, canvas, layer), frame, frame + self.hold - 1)
            frame += self.hold
        scene.frame_start = self.start
        scene.frame_end = frame - 1
        refresh(scene)
        return {"FINISHED"}


class BLIX_OT_cel_insert(_CelOperator):
    """Add a new cel after the active one with the same length, shifting later cels"""

    bl_idname = "blix.cel_insert"
    bl_label = "Insert Cel"
    bl_options = {"REGISTER", "INTERNAL"}

    @classmethod
    def poll(cls, context: bpy.types.Context) -> bool:
        from . import layers

        if not super().poll(context):
            return False
        canvas = layers.resolve_canvas(context)
        scene = context.scene
        assert canvas is not None and scene is not None
        layer = layers.active_layer(canvas)
        return layer is not None and layer.cel and track(scene, canvas, layer) is not None

    def execute(self, context: bpy.types.Context) -> set[OperatorReturnItems]:
        from . import layers

        canvas = layers.resolve_canvas(context)
        scene = context.scene
        assert canvas is not None and scene is not None
        layer = layers.active_layer(canvas)
        first, last = frame_range(scene, track(scene, canvas, layer))
        hold = max(last - first + 1, 1)
        with layers._undo_step(context, canvas, "Blix Insert Cel"):
            layers.add_layer(canvas, layers.next_layer_number(canvas))
            sync(scene, canvas)
            _shift(scene, canvas, last, hold)
            added = track(scene, canvas, layers.active_layer(canvas))
            set_range(scene, added, last + 1, last + hold)
            scene.frame_end += hold
        scene.frame_set(last + 1)
        return {"FINISHED"}


class BLIX_OT_cels_export(_CelOperator, ExportHelper):
    """Export the composite of every frame in the scene range"""

    bl_idname = "blix.cels_export"
    bl_label = "Export Animation"
    bl_options = {"REGISTER"}

    filename_ext = ".png"
    filter_glob: bpy.props.StringProperty(default="*.png", options={"HIDDEN"})
    mode: bpy.props.EnumProperty(name="Mode", items=MODE_ITEMS, default="STRIP")

    def invoke(
        self, context: bpy.types.Context, event: bpy.types.Event
    ) -> set[OperatorReturnItems]:
        from . import layers

        canvas = layers.resolve_canvas(context)
        assert canvas is not None
        cast(Any, self).filepath = f"{canvas.name}_anim.png"
        return cast("set[OperatorReturnItems]", ExportHelper.invoke(self, context, event))

    def execute(self, context: bpy.types.Context) -> set[OperatorReturnItems]:
        from . import layers, stacking

        canvas = layers.resolve_canvas(context)
        scene = context.scene
        assert canvas is not None and scene is not None
        layers.sync_canvas(canvas)
        path = Path(cast(Any, self).filepath)
        current = scene.frame_current
        frames = range(scene.frame_start, scene.frame_end + 1)
        strip: list[np.ndarray] = []
        try:
            for frame in frames:
                scene.frame_set(frame)
                pixels = layers.composite_pixels(canvas)
                if self.mode == "STRIP":
                    strip.append(pixels)
                    continue
                stacking.save_strip(
                    pixels, str(path.with_name(f"{path.stem}_{frame:04d}{path.suffix}"))
                )
            if strip:
                stacking.save_strip(np.concatenate(strip, axis=1), str(path))
        except (RuntimeError, OSError) as error:
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}
        finally:
            scene.frame_set(current)
        self.report({"INFO"}, f"{len(frames)} frames to {path.name}")
        return {"FINISHED"}


_classes = (
    BlixCel,
    BLIX_OT_cels_enable,
    BLIX_OT_cels_disable,
    BLIX_OT_cels_layout,
    BLIX_OT_cel_insert,
    BLIX_OT_cels_export,
)


def register() -> None:
    for cls in _classes:
        bpy.utils.register_class(cls)
    scene_cls = cast(Any, bpy.types.Scene)
    scene_cls.blix_cels = bpy.props.CollectionProperty(type=BlixCel)
    scene_cls.blix_cel_follow = bpy.props.BoolProperty(
        name="Follow Frame",
        description="Make the cel shown at the current frame the active layer",
        default=True,
    )
    bpy.app.handlers.frame_change_post.append(_frame_changed)


def unregister() -> None:
    bpy.app.handlers.frame_change_post.remove(_frame_changed)
    scene_cls = cast(Any, bpy.types.Scene)
    del scene_cls.blix_cel_follow
    del scene_cls.blix_cels
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
