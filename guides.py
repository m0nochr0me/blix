"""Guide data model on Image and interactive guide operators."""

import bpy

from . import overlay

GRAB_RADIUS = 6.0


class BlixGuide(bpy.types.PropertyGroup):
    orientation: bpy.props.EnumProperty(
        name="Orientation",
        items=(
            ('VERTICAL', "Vertical", "Guide along Y at X position"),
            ('HORIZONTAL', "Horizontal", "Guide along X at Y position"),
        ),
        default='VERTICAL',
    )
    position: bpy.props.FloatProperty(name="Position", default=0.0)


def _image(context: bpy.types.Context) -> bpy.types.Image | None:
    space = context.space_data
    if space is None or space.type != 'IMAGE_EDITOR':
        return None
    return space.image


def _find_near(
    region: bpy.types.Region, image: bpy.types.Image, mx: float, my: float
) -> int:
    best = -1
    best_dist = GRAB_RADIUS * overlay.ui_scale()
    for index, guide in enumerate(image.blix_guides):
        if guide.orientation == 'VERTICAL':
            gx, _ = overlay.image_to_region(region, image, guide.position, 0.0)
            dist = abs(mx - gx)
        else:
            _, gy = overlay.image_to_region(region, image, 0.0, guide.position)
            dist = abs(my - gy)
        if dist <= best_dist:
            best_dist = dist
            best = index
    return best


class BLIX_OT_guide_add(bpy.types.Operator):
    """Add guide at image center"""

    bl_idname = "blix.guide_add"
    bl_label = "Add Guide"
    bl_options = {'REGISTER', 'UNDO'}

    orientation: bpy.props.EnumProperty(
        name="Orientation",
        items=(
            ('VERTICAL', "Vertical", ""),
            ('HORIZONTAL', "Horizontal", ""),
        ),
        default='VERTICAL',
    )

    @classmethod
    def poll(cls, context: bpy.types.Context) -> bool:
        return _image(context) is not None

    def execute(self, context: bpy.types.Context) -> set[str]:
        image = _image(context)
        guide = image.blix_guides.add()
        guide.orientation = self.orientation
        extent = image.size[1] if self.orientation == 'HORIZONTAL' else image.size[0]
        guide.position = extent // 2
        image.blix_guides_index = len(image.blix_guides) - 1
        overlay.tag_redraw(context)
        return {'FINISHED'}


class BLIX_OT_guide_remove(bpy.types.Operator):
    """Remove active guide"""

    bl_idname = "blix.guide_remove"
    bl_label = "Remove Guide"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context: bpy.types.Context) -> bool:
        image = _image(context)
        return image is not None and len(image.blix_guides) > 0

    def execute(self, context: bpy.types.Context) -> set[str]:
        image = _image(context)
        index = min(image.blix_guides_index, len(image.blix_guides) - 1)
        image.blix_guides.remove(index)
        image.blix_guides_index = min(index, len(image.blix_guides) - 1)
        overlay.tag_redraw(context)
        return {'FINISHED'}


class BLIX_OT_guide_clear(bpy.types.Operator):
    """Remove all guides"""

    bl_idname = "blix.guide_clear"
    bl_label = "Clear Guides"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context: bpy.types.Context) -> bool:
        image = _image(context)
        return image is not None and len(image.blix_guides) > 0

    def execute(self, context: bpy.types.Context) -> set[str]:
        image = _image(context)
        image.blix_guides.clear()
        image.blix_guides_index = 0
        overlay.tag_redraw(context)
        return {'FINISHED'}


class BLIX_OT_guide_drag(bpy.types.Operator):
    """Drag guide: from ruler band to create, Ctrl near guide to move, Shift for free positioning"""

    bl_idname = "blix.guide_drag"
    bl_label = "Drag Guide"
    bl_options = {'REGISTER', 'UNDO', 'INTERNAL'}

    _index: int
    _is_new: bool
    _original: float

    def invoke(self, context: bpy.types.Context, event: bpy.types.Event) -> set[str]:
        image = _image(context)
        if image is None or image.size[0] == 0 or image.size[1] == 0:
            return {'PASS_THROUGH'}
        region = context.region
        mx, my = event.mouse_region_x, event.mouse_region_y
        band = overlay.ruler_px()
        rulers = context.scene.blix_show_rulers

        if event.ctrl:
            index = _find_near(region, image, mx, my)
            if index < 0:
                return {'PASS_THROUGH'}
            self._index = index
            self._is_new = False
            self._original = image.blix_guides[index].position
        elif rulers and my >= region.height - band:
            self._start(image, 'HORIZONTAL')
        elif rulers and mx <= band:
            self._start(image, 'VERTICAL')
        else:
            return {'PASS_THROUGH'}

        context.window_manager.modal_handler_add(self)
        return {'RUNNING_MODAL'}

    def _start(self, image: bpy.types.Image, orientation: str) -> None:
        guide = image.blix_guides.add()
        guide.orientation = orientation
        self._index = len(image.blix_guides) - 1
        self._is_new = True
        self._original = 0.0

    def modal(self, context: bpy.types.Context, event: bpy.types.Event) -> set[str]:
        image = _image(context)
        if image is None:
            return {'CANCELLED'}
        guide = image.blix_guides[self._index]

        if event.type == 'MOUSEMOVE':
            x, y = overlay.region_to_image(
                context.region, image, event.mouse_region_x, event.mouse_region_y
            )
            value = y if guide.orientation == 'HORIZONTAL' else x
            guide.position = value if event.shift else round(value)
            overlay.tag_redraw(context)
            return {'RUNNING_MODAL'}

        if event.type == 'LEFTMOUSE' and event.value == 'RELEASE':
            extent = image.size[1] if guide.orientation == 'HORIZONTAL' else image.size[0]
            if guide.position < 0 or guide.position > extent:
                image.blix_guides.remove(self._index)
            overlay.tag_redraw(context)
            return {'FINISHED'}

        if event.type in {'ESC', 'RIGHTMOUSE'}:
            if self._is_new:
                image.blix_guides.remove(self._index)
            else:
                guide.position = self._original
            overlay.tag_redraw(context)
            return {'CANCELLED'}

        return {'RUNNING_MODAL'}


_classes = (
    BlixGuide,
    BLIX_OT_guide_add,
    BLIX_OT_guide_remove,
    BLIX_OT_guide_clear,
    BLIX_OT_guide_drag,
)

_keymaps: list[tuple[bpy.types.KeyMap, bpy.types.KeyMapItem]] = []


def _register_keymap() -> None:
    keyconfig = bpy.context.window_manager.keyconfigs.addon
    if keyconfig is None:
        return
    keymap = keyconfig.keymaps.new(name="Image", space_type='IMAGE_EDITOR')
    for ctrl in (False, True):
        item = keymap.keymap_items.new(
            BLIX_OT_guide_drag.bl_idname, 'LEFTMOUSE', 'PRESS', ctrl=ctrl
        )
        _keymaps.append((keymap, item))


def _unregister_keymap() -> None:
    for keymap, item in _keymaps:
        keymap.keymap_items.remove(item)
    _keymaps.clear()


def register() -> None:
    for cls in _classes:
        bpy.utils.register_class(cls)
    bpy.types.Image.blix_guides = bpy.props.CollectionProperty(type=BlixGuide)
    bpy.types.Image.blix_guides_index = bpy.props.IntProperty(default=0)
    _register_keymap()


def unregister() -> None:
    _unregister_keymap()
    del bpy.types.Image.blix_guides_index
    del bpy.types.Image.blix_guides
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
