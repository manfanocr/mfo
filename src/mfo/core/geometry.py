"""Geometric primitives shared by the vision, render, and core layers.

Coordinates are in source-image pixel space with the origin at the top-left. Values are
floats so they survive sub-pixel scaling during preprocessing.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class Point(BaseModel):
    """A 2D point in image pixel space."""

    model_config = ConfigDict(extra="forbid")

    x: float
    y: float


class BBox(BaseModel):
    """An axis-aligned bounding box (top-left origin, width/height non-negative)."""

    model_config = ConfigDict(extra="forbid")

    x: float
    y: float
    width: float = Field(ge=0)
    height: float = Field(ge=0)

    @property
    def right(self) -> float:
        return self.x + self.width

    @property
    def bottom(self) -> float:
        return self.y + self.height

    @property
    def area(self) -> float:
        return self.width * self.height
