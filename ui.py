"""Image editor sidebar panels."""

from typing import Any, cast

import bpy


def _space(context: bpy.types.Context) -> bpy.types.SpaceImageEditor:
    return cast(bpy.types.SpaceImageEditor, context.space_data)


class BLIX_PT_grid(bpy.types.Panel):
    bl_space_type = "IMAGE_EDITOR"
    bl_region_type = "UI"
    bl_category = "Blix"
    bl_label = "Pixel Grid"

    def draw(self, context: bpy.types.Context) -> None:
        uv_editor = _space(context).uv_editor
        layout = self.layout
        assert layout is not None
        col = layout.column()
        col.prop(uv_editor, "grid_shape_source", text="Shape")
        col.prop(uv_editor, "show_grid_over_image", text="Over Image")
        if uv_editor.grid_shape_source == "FIXED":
            col.prop(uv_editor, "custom_grid_subdivisions", text="Subdivisions")


class BLIX_UL_guides(bpy.types.UIList):
    def draw_item(
        self,
        context: bpy.types.Context | None,
        layout: bpy.types.UILayout,
        data: Any | None,
        item: Any | None,
        icon: int | None,
        active_data: Any,
        active_property: str | None,
        index: int | None = 0,
        flt_flag: int | None = 0,
    ) -> None:
        assert item is not None
        row = layout.row(align=True)
        axis = "Y" if item.orientation == "HORIZONTAL" else "X"
        row.prop(item, "position", text=axis, emboss=False)


class BLIX_PT_guides(bpy.types.Panel):
    bl_space_type = "IMAGE_EDITOR"
    bl_region_type = "UI"
    bl_category = "Blix"
    bl_label = "Rulers & Guides"

    def draw(self, context: bpy.types.Context) -> None:
        layout = self.layout
        scene = context.scene
        assert layout is not None
        assert scene is not None
        row = layout.row(align=True)
        row.prop(scene, "blix_show_rulers", toggle=True)
        row.prop(scene, "blix_show_guides", toggle=True)

        image = _space(context).image
        if image is None:
            return
        row = layout.row()
        row.template_list(
            "BLIX_UL_guides", "", image, "blix_guides", image, "blix_guides_index", rows=3
        )
        col = row.column(align=True)
        cast(Any, col.operator("blix.guide_add", text="V")).orientation = "VERTICAL"
        cast(Any, col.operator("blix.guide_add", text="H")).orientation = "HORIZONTAL"
        col.separator()
        col.operator("blix.guide_remove", text="", icon="REMOVE")
        col.operator("blix.guide_clear", text="", icon="X")


class BLIX_PT_select(bpy.types.Panel):
    bl_space_type = "IMAGE_EDITOR"
    bl_region_type = "UI"
    bl_category = "Blix"
    bl_label = "Selection"

    def draw(self, context: bpy.types.Context) -> None:
        layout = self.layout
        assert layout is not None
        row = layout.row(align=True)
        cast(Any, row.operator("blix.select_rotate90", text="90 CCW")).turns = 1
        cast(Any, row.operator("blix.select_rotate90", text="180")).turns = 2
        cast(Any, row.operator("blix.select_rotate90", text="90 CW")).turns = 3
        row = layout.row(align=True)
        cast(Any, row.operator("blix.select_flip", text="Flip H")).horizontal = True
        cast(Any, row.operator("blix.select_flip", text="Flip V")).horizontal = False


_classes = (BLIX_PT_grid, BLIX_UL_guides, BLIX_PT_guides, BLIX_PT_select)


def register() -> None:
    for cls in _classes:
        bpy.utils.register_class(cls)


def unregister() -> None:
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
