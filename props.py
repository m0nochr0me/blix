"""Typed access to Blix RNA properties registered at runtime."""

from typing import Any, cast

import bpy


def guides(image: bpy.types.Image) -> Any:
    return cast(Any, image).blix_guides


def guides_index(image: bpy.types.Image) -> int:
    return cast(Any, image).blix_guides_index


def set_guides_index(image: bpy.types.Image, value: int) -> None:
    cast(Any, image).blix_guides_index = value


def show_rulers(scene: bpy.types.Scene) -> bool:
    return cast(Any, scene).blix_show_rulers


def show_guides(scene: bpy.types.Scene) -> bool:
    return cast(Any, scene).blix_show_guides
