"""Direct pixel writes registered with Blender's image undo stack."""

from typing import Any, cast

import bpy


def record(context: bpy.types.Context, image: bpy.types.Image) -> None:
    """Store image pixels as an undo step; bracket every write with a call before and after."""
    with context.temp_override(edit_image=image):
        cast(Any, bpy.ops.image).invert()
