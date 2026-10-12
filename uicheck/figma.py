"""Fetch a frame from the Figma REST API: rendered PNG + style specs of its layers."""

from __future__ import annotations

import os
import re
from difflib import SequenceMatcher
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

import httpx

from .color import to_hex
from .models import Analysis, Rect

API = "https://api.figma.com/v1"
BOX_TYPES = {"FRAME", "RECTANGLE", "INSTANCE", "COMPONENT", "COMPONENT_SET", "SECTION"}


class FigmaError(Exception):
    pass


def parse_url(url: str) -> tuple[str, str | None]:
    """Return (file_key, node_id) from a Figma share link."""
    u = urlparse(url.strip())
    if "figma.com" not in u.netloc:
        raise FigmaError("Link không phải link Figma.")
    m = re.match(r"^/(?:file|design|proto|board)/([A-Za-z0-9]+)", u.path)
    if not m:
        raise FigmaError("Không đọc được file key trong link Figma.")
    key = m.group(1)
    # branch links: /design/<key>/branch/<branchKey>/...
    b = re.match(r"^/(?:file|design)/[A-Za-z0-9]+/branch/([A-Za-z0-9]+)", u.path)
    if b:
        key = b.group(1)
    node = parse_qs(u.query).get("node-id", [None])[0]
    if node:
        node = unquote(node).replace("-", ":")
    return key, node


def _client(token: str) -> httpx.Client:
    return httpx.Client(headers={"X-Figma-Token": token}, timeout=60, follow_redirects=True)


def _get(client: httpx.Client, url: str, **params: Any) -> dict:
    r = client.get(url, params=params)
    if r.status_code == 403:
        raise FigmaError("Figma từ chối truy cập (403): token sai hoặc không có quyền xem file.")
    if r.status_code == 404:
        raise FigmaError("Không tìm thấy file/node trên Figma (404).")
    if r.status_code == 429:
        raise FigmaError("Figma đang giới hạn số request (429), thử lại sau ít phút.")
    if r.status_code >= 400:
        raise FigmaError(f"Figma API lỗi {r.status_code}: {r.text[:200]}")
    return r.json()


def _paint_hex(paints: list[dict] | None, node_opacity: float = 1.0) -> str | None:
    for p in paints or []:
        if p.get("visible") is False or p.get("type") != "SOLID":
            continue
        c = p.get("color", {})
        a = c.get("a", 1) * p.get("opacity", 1) * node_opacity
        if a <= 0.01:
            continue
        hexv = to_hex((c.get("r", 0) * 255, c.get("g", 0) * 255, c.get("b", 0) * 255))
        return hexv if a >= 0.99 else f"{hexv} @{round(a * 100)}%"
    return None


def extract_specs(root: dict) -> dict[str, Any]:
    """Collect texts and filled/stroked containers of a frame (coordinates relative to it)."""
    bb = root.get("absoluteBoundingBox") or {}
    ox, oy = bb.get("x", 0), bb.get("y", 0)
    texts: list[dict] = []
    boxes: list[dict] = []

    def rect_of(n: dict) -> list[float] | None:
        b = n.get("absoluteBoundingBox")
        if not b:
            return None
        return [b["x"] - ox, b["y"] - oy, b["width"], b["height"]]

    def walk(n: dict, opacity: float) -> None:
        if n.get("visible") is False:
            return
        op = opacity * n.get("opacity", 1)
        r = rect_of(n)
        t = n.get("type")
        if t == "TEXT" and r:
            st = n.get("style", {})
            texts.append(
                {
                    "name": n.get("name"),
                    "characters": n.get("characters", ""),
                    "fontFamily": st.get("fontFamily"),
                    "fontWeight": st.get("fontWeight"),
                    "fontSize": st.get("fontSize"),
                    "lineHeight": st.get("lineHeightPx"),
                    "letterSpacing": st.get("letterSpacing"),
                    "italic": st.get("italic", False),
                    "color": _paint_hex(n.get("fills"), op),
                    "rect": r,
                }
            )
        elif t in BOX_TYPES and r and n is not root:
            fill = _paint_hex(n.get("fills"), op)
            stroke = _paint_hex(n.get("strokes"), op)
            if fill or stroke:
                box = {
                    "name": n.get("name"),
                    "type": t,
                    "fill": fill,
                    "stroke": stroke,
                    "strokeWeight": n.get("strokeWeight") if stroke else None,
                    "cornerRadius": n.get("cornerRadius"),
                    "rect": r,
                }
                if n.get("layoutMode") and n.get("layoutMode") != "NONE":
                    box["layout"] = {
                        "mode": n.get("layoutMode"),
                        "padding": [n.get(k, 0) for k in ("paddingTop", "paddingRight", "paddingBottom", "paddingLeft")],
                        "itemSpacing": n.get("itemSpacing", 0),
                    }
                boxes.append(box)
        for c in n.get("children", []) or []:
            walk(c, op)

    for c in root.get("children", []) or []:
        walk(c, root.get("opacity", 1))
    return {
        "name": root.get("name"),
        "width": bb.get("width"),
        "height": bb.get("height"),
        "background": _paint_hex(root.get("fills")),
        "texts": texts,
        "boxes": boxes,
    }


def fetch(url: str, token: str | None = None) -> tuple[bytes, float, dict[str, Any]]:
    """Return (png bytes, export scale, specs) for the frame a Figma link points at."""
    token = token or os.environ.get("FIGMA_TOKEN")
    if not token:
        raise FigmaError("Cần Figma personal access token (nhập ở form hoặc đặt biến môi trường FIGMA_TOKEN).")
    key, node_id = parse_url(url)
    try:
        return _fetch(key, node_id, token)
    except httpx.HTTPError as e:
        raise FigmaError(f"Không kết nối được tới Figma API ({type(e).__name__}: {e}).") from e


def _fetch(key: str, node_id: str | None, token: str) -> tuple[bytes, float, dict[str, Any]]:
    with _client(token) as client:
        if not node_id:
            doc = _get(client, f"{API}/files/{key}", depth=2)
            pages = doc.get("document", {}).get("children", [])
            frames = [c for p in pages for c in p.get("children", []) if c.get("type") in BOX_TYPES]
            if not frames:
                raise FigmaError("Link không có node-id và file không có frame nào. Hãy copy link tới frame cụ thể.")
            node_id = frames[0]["id"]
        nodes = _get(client, f"{API}/files/{key}/nodes", ids=node_id)
        node = (nodes.get("nodes") or {}).get(node_id)
        if not node or not node.get("document"):
            raise FigmaError(f"Không tìm thấy node {node_id} trong file.")
        root = node["document"]
        specs = extract_specs(root)
        width = specs.get("width") or 375
        scale = 3 if width <= 640 else 2
        imgs = _get(client, f"{API}/images/{key}", ids=node_id, scale=scale, format="png", use_absolute_bounds="true")
        if imgs.get("err"):
            raise FigmaError(f"Figma không render được frame: {imgs['err']}")
        img_url = (imgs.get("images") or {}).get(node_id)
        if not img_url:
            raise FigmaError("Figma không trả về ảnh cho frame này (frame rỗng hoặc bị ẩn?).")
        png = httpx.get(img_url, timeout=120, follow_redirects=True)
        png.raise_for_status()
    specs.update({"file_key": key, "node_id": node_id})
    return png.content, float(scale), specs


def annotate(analysis: Analysis, specs: dict[str, Any], top_offset: float = 0.0) -> None:
    """Attach Figma layer specs to the elements detected in the design render.

    top_offset: design px cropped from the top of the render (ignore_top).
    """
    s = analysis.scale

    def to_rect(r: list[float]) -> Rect:
        return Rect(r[0] * s, (r[1] - top_offset) * s, r[2] * s, r[3] * s)

    texts = [(t, to_rect(t["rect"])) for t in specs.get("texts", [])]
    boxes = [(b, to_rect(b["rect"])) for b in specs.get("boxes", [])]
    for e in analysis.elements:
        if e.kind == "text":
            best, best_score = None, 0.0
            for t, r in texts:
                if not r.expanded(2 * s).contains(Rect(e.rect.cx, e.rect.cy, 0, 0)):
                    continue
                chars = re.sub(r"\s+", " ", t["characters"] or "").lower()
                sim = SequenceMatcher(None, e.text.lower(), chars).find_longest_match(0, len(e.text), 0, len(chars)).size
                score = sim / max(len(e.text), 1) + 1 / (1 + r.area / 1e5)
                if score > best_score:
                    best, best_score = t, score
            if best:
                e.figma = {k: best[k] for k in ("name", "fontFamily", "fontWeight", "fontSize", "lineHeight", "letterSpacing", "color")}
        elif e.kind == "box":
            best, best_iou = None, 0.7
            for b, r in boxes:
                iou = e.rect.iou(r)
                if iou > best_iou:
                    best, best_iou = b, iou
            if best:
                e.figma = {k: best.get(k) for k in ("name", "fill", "stroke", "cornerRadius", "layout")}
