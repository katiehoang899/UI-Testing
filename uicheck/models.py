"""Data structures shared by the detection, matching and comparison steps."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np

from .color import RGB, to_hex


@dataclass
class Rect:
    x: float
    y: float
    w: float
    h: float

    def __post_init__(self) -> None:
        # Values often come from numpy; keep plain floats so results serialize to JSON.
        self.x, self.y, self.w, self.h = float(self.x), float(self.y), float(self.w), float(self.h)

    @property
    def right(self) -> float:
        return self.x + self.w

    @property
    def bottom(self) -> float:
        return self.y + self.h

    @property
    def cx(self) -> float:
        return self.x + self.w / 2

    @property
    def cy(self) -> float:
        return self.y + self.h / 2

    @property
    def area(self) -> float:
        return max(self.w, 0) * max(self.h, 0)

    def shifted(self, dx: float, dy: float) -> "Rect":
        return Rect(self.x + dx, self.y + dy, self.w, self.h)

    def expanded(self, d: float) -> "Rect":
        return Rect(self.x - d, self.y - d, self.w + 2 * d, self.h + 2 * d)

    def intersection(self, other: "Rect") -> float:
        w = min(self.right, other.right) - max(self.x, other.x)
        h = min(self.bottom, other.bottom) - max(self.y, other.y)
        return w * h if w > 0 and h > 0 else 0.0

    def iou(self, other: "Rect") -> float:
        inter = self.intersection(other)
        union = self.area + other.area - inter
        return inter / union if union > 0 else 0.0

    def contains(self, other: "Rect", tol: float = 0.0) -> bool:
        return (
            other.x >= self.x - tol
            and other.y >= self.y - tol
            and other.right <= self.right + tol
            and other.bottom <= self.bottom + tol
        )

    def v_overlap(self, other: "Rect") -> float:
        return max(0.0, min(self.bottom, other.bottom) - max(self.y, other.y))

    def h_overlap(self, other: "Rect") -> float:
        return max(0.0, min(self.right, other.right) - max(self.x, other.x))

    def to_list(self, scale: float = 1.0) -> list[float]:
        return [round(v / scale, 2) for v in (self.x, self.y, self.w, self.h)]

    @staticmethod
    def union(rects: list["Rect"]) -> "Rect":
        x = min(r.x for r in rects)
        y = min(r.y for r in rects)
        return Rect(x, y, max(r.right for r in rects) - x, max(r.bottom for r in rects) - y)


@dataclass
class Element:
    """A visual element found in a screenshot.

    kind: "text"    - one line of text (tight ink bounds)
          "box"     - a solid filled / bordered container (card, button, input, bar...)
          "graphic" - any other non-text content (icon, image, avatar, divider...)
    """

    id: str
    kind: str
    rect: Rect
    text: str = ""
    conf: float = 0.0
    color: RGB | None = None  # text ink color, or box fill color
    bg_color: RGB | None = None  # color behind a text line
    border_color: RGB | None = None
    stroke: float = 0.0  # average stroke width of text glyphs (px)
    parent: str | None = None
    name: str | None = None  # human readable name, e.g. 'Khối chứa "Email"'
    figma: dict[str, Any] | None = None
    mask: np.ndarray | None = field(default=None, repr=False)  # ink mask of a text line

    def label(self, max_len: int = 32) -> str:
        if self.kind == "text":
            t = self.text if len(self.text) <= max_len else self.text[: max_len - 1] + "…"
            return f'"{t}"'
        base = {"box": "Khối", "graphic": "Icon/ảnh"}.get(self.kind, self.kind)
        if self.figma and self.figma.get("name"):
            return f"{base} “{self.figma['name']}”"
        return self.name or f"{base} {self.id}"

    def to_dict(self, scale: float) -> dict[str, Any]:
        d: dict[str, Any] = {
            "id": self.id,
            "kind": self.kind,
            "rect": self.rect.to_list(),
            "rect_px": self.rect.to_list(scale),
            "parent": self.parent,
            "label": self.label(),
        }
        if self.kind == "text":
            d["text"] = self.text
            d["color"] = to_hex(self.color)
        elif self.kind == "box":
            d["color"] = to_hex(self.color)
            d["border"] = to_hex(self.border_color)
        if self.figma:
            d["figma"] = self.figma
        return d


@dataclass
class Analysis:
    """Everything detected in one (normalized) screenshot."""

    image: np.ndarray  # BGR, normalized to `scale` px per design px
    scale: float  # analysis pixels per CSS/design px
    source_scale: float  # device pixel ratio of the uploaded image
    background: RGB
    elements: list[Element]
    lossy: bool = False  # decoded from a lossy format (JPEG / lossy WEBP)

    @property
    def width(self) -> int:
        return int(self.image.shape[1])

    @property
    def height(self) -> int:
        return int(self.image.shape[0])

    def by_id(self) -> dict[str, Element]:
        return {e.id: e for e in self.elements}

    def of_kind(self, kind: str) -> list[Element]:
        return [e for e in self.elements if e.kind == kind]


@dataclass
class Options:
    tol_spacing: float = 2.0  # px (design units)
    tol_font_size: float = 1.0  # px
    tol_color: float = 3.0  # CIEDE2000
    checks: tuple[str, ...] = ("spacing", "padding", "font", "color", "size")
    lang: str = "vie+eng"
    design_scale: float | None = None  # None = auto
    dev_scale: float | None = None
    ignore_top: float = 0.0  # px (design units) cut from the top of both images, e.g. status bar
    ignore_bottom: float = 0.0
