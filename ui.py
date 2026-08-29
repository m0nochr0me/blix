"""Image editor sidebar panels."""

import bpy


class BLIX_PT_grid(bpy.types.Panel):
    bl_space_type = 'IMAGE_EDITOR'
    bl_region_type = 'UI'
    bl_category = "Blix"
    bl_label = "Pixel Grid"

    def draw(self, context: bpy.types.Context) -> None:
        uv_editor = context.space_data.uv_editor
        col = self.layout.column()
        col.prop(uv_editor, "grid_shape_source", text="Shape")
        col.prop(uv_editor, "show_grid_over_image", text="Over Image")
        if uv_editor.grid_shape_source == 'FIXED':
            col.prop(uv_editor, "custom_grid_subdivisions", text="Subdivisions")


class BLIX_UL_guides(bpy.types.UIList):
    def draw_item(
        self,
        context: bpy.types.Context,
        layout: bpy.types.UILayout,
        data: bpy.types.Image,
        item: bpy.types.PropertyGroup,
        icon: int,
        active_data: bpy.types.Image,
        active_property: str,
        index: int = 0,
        flt_flag: int = 0,
    ) -> None:
        row = layout.row(align=True)
        axis = "Y" if item.orientation == 'HORIZONTAL' else "X"
        row.prop(item, "position", text=axis, emboss=False)


class BLIX_PT_guides(bpy.types.Panel):
    bl_space_type = 'IMAGE_EDITOR'
    bl_region_type = 'UI'
    bl_category = "Blix"
    bl_label = "Rulers & Guides"

    def draw(self, context: bpy.types.Context) -> None:
        layout = self.layout
        scene = context.scene
        row = layout.row(align=True)
        row.prop(scene, "blix_show_rulers", toggle=True)
        row.prop(scene, "blix_show_guides", toggle=True)

        image = context.space_data.image
        if image is None:
            return
        row = layout.row()
        row.template_list(
            "BLIX_UL_guides", "", image, "blix_guides", image, "blix_guides_index", rows=3
        )
        col = row.column(align=True)
        col.operator("blix.guide_add", text="V").orientation = 'VERTICAL'
        col.operator("blix.guide_add", text="H").orientation = 'HORIZONTAL'
        col.separator()
        col.operator("blix.guide_remove", text="", icon='REMOVE')
        col.operator("blix.guide_clear", text="", icon='X')


_classes = (BLIX_PT_grid, BLIX_UL_guides, BLIX_PT_guides)


def register() -> None:
    for cls in _classes:
        bpy.utils.register_class(cls)


def unregister() -> None:
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
