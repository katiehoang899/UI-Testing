"""Render synthetic mobile screens so the checker can be tested end to end.

`render(**overrides)` draws a 375px wide screen at the given pixel ratio. Every
layout/style value can be overridden to simulate a dev implementation that
drifts from the design.
"""

from __future__ import annotations

import io
import os

from PIL import Image, ImageDraw, ImageFont

FONT_DIRS = ["/usr/share/fonts/opentype/inter", "/usr/share/fonts/truetype/dejavu"]
WEIGHT_FILES = {
    400: ["Inter-Regular.otf", "DejaVuSans.ttf"],
    600: ["Inter-SemiBold.otf", "DejaVuSans-Bold.ttf"],
    700: ["Inter-Bold.otf", "DejaVuSans-Bold.ttf"],
}
SERIF_FILES = ["/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf"]

DEFAULTS = dict(
    scale=3,
    width=375,
    height=640,
    bg="#F5F6FA",
    margin=16,
    title_size=24,
    title_weight=700,
    title_color="#1A1A2E",
    subtitle_size=14,
    subtitle_color="#6B7280",
    gap_title_subtitle=8,
    gap_subtitle_card=24,
    card_bg="#FFFFFF",
    card_padding=16,
    card_radius=12,
    label_size=14,
    label_weight=600,
    label_color="#111827",
    input_border="#D1D5DB",
    input_height=44,
    gap_label_input=8,
    gap_fields=16,
    button_bg="#4F46E5",
    button_text_color="#FFFFFF",
    button_height=48,
    button_text_size=16,
    gap_card_button=24,
    serif_label=False,
    icon=False,  # draw a round avatar left of a "Xin chào" line under the button
    icon_gap=12,
    icon_size=32,
)


def font_available() -> bool:
    return _font_path(400) is not None


def _font_path(weight: int, serif: bool = False) -> str | None:
    if serif:
        return next((p for p in SERIF_FILES if os.path.exists(p)), None)
    for name in WEIGHT_FILES[weight]:
        for d in FONT_DIRS:
            p = os.path.join(d, name)
            if os.path.exists(p):
                return p
    return None


def _font(size: float, weight: int, s: float, serif: bool = False) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(_font_path(weight, serif), int(round(size * s)))


def render(**overrides) -> bytes:
    o = {**DEFAULTS, **overrides}
    s = o["scale"]
    W, H = int(o["width"] * s), int(o["height"] * s)
    img = Image.new("RGB", (W, H), o["bg"])
    d = ImageDraw.Draw(img)

    def text(x: float, y: float, value: str, size: float, weight: int, color: str, serif: bool = False) -> float:
        """Draw text so that its line box starts at y. Returns the line box bottom."""
        f = _font(size, weight, s, serif)
        ascent, descent = f.getmetrics()
        d.text((x * s, y * s), value, font=f, fill=color)
        return y + (ascent + descent) / s

    m = o["margin"]
    y = 40
    y = text(m, y, "Đăng nhập", o["title_size"], o["title_weight"], o["title_color"])
    y += o["gap_title_subtitle"]
    y = text(m, y, "Chào mừng bạn quay trở lại", o["subtitle_size"], 400, o["subtitle_color"])
    y += o["gap_subtitle_card"]

    # card
    card_top = y
    p = o["card_padding"]
    inner_y = card_top + p
    fields = ["Email", "Mật khẩu"]
    field_boxes = []
    for i, label in enumerate(fields):
        if i:
            inner_y += o["gap_fields"]
        inner_y = text(m + p, inner_y, label, o["label_size"], o["label_weight"], o["label_color"], o["serif_label"])
        inner_y += o["gap_label_input"]
        field_boxes.append((m + p, inner_y, o["width"] - m - p, inner_y + o["input_height"]))
        inner_y += o["input_height"]
    card_bottom = inner_y + p
    d.rounded_rectangle(
        (m * s, card_top * s, (o["width"] - m) * s, card_bottom * s), radius=o["card_radius"] * s, fill=o["card_bg"]
    )
    # redraw the card content on top of the card background
    inner_y = card_top + p
    for i, label in enumerate(fields):
        if i:
            inner_y += o["gap_fields"]
        inner_y = text(m + p, inner_y, label, o["label_size"], o["label_weight"], o["label_color"], o["serif_label"])
        inner_y += o["gap_label_input"]
        x0, y0, x1, y1 = field_boxes[i]
        d.rounded_rectangle((x0 * s, y0 * s, x1 * s, y1 * s), radius=8 * s, fill="#FFFFFF", outline=o["input_border"], width=max(1, int(s)))
        inner_y += o["input_height"]

    # button
    by = card_bottom + o["gap_card_button"]
    d.rounded_rectangle((m * s, by * s, (o["width"] - m) * s, (by + o["button_height"]) * s), radius=10 * s, fill=o["button_bg"])
    f = _font(o["button_text_size"], 600, s)
    label = "Tiếp tục"
    bbox = d.textbbox((0, 0), label, font=f)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    d.text(
        ((W - tw) / 2 - bbox[0], (by + o["button_height"] / 2) * s - th / 2 - bbox[1]),
        label,
        font=f,
        fill=o["button_text_color"],
    )

    if o["icon"]:
        iy = by + o["button_height"] + 24
        sz = o["icon_size"]
        d.ellipse((m * s, iy * s, (m + sz) * s, (iy + sz) * s), fill="#F59E0B")
        f = _font(14, 400, s)
        asc, desc = f.getmetrics()
        d.text(((m + sz + o["icon_gap"]) * s, (iy + sz / 2) * s - (asc + desc) / 2), "Xin chào Minh", font=f, fill="#111827")

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


if __name__ == "__main__":
    import sys

    out = sys.argv[1] if len(sys.argv) > 1 else "synthetic.png"
    with open(out, "wb") as fh:
        fh.write(render())
    print("wrote", out)
