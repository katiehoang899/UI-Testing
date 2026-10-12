"""Pair design elements with dev elements and report spacing / font / color differences.

Text content is only used as an anchor to pair elements (it is never reported);
graphics are compared by position/size only, never by what they show.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher
from statistics import median
from typing import Any

import cv2
import numpy as np

from .color import delta_e, to_hex
from .models import Analysis, Element, Options, Rect

WEIGHT_NAMES = {
    100: "Thin", 200: "ExtraLight", 300: "Light", 400: "Regular", 500: "Medium",
    600: "SemiBold", 700: "Bold", 800: "ExtraBold", 900: "Black",
}

GROUP_OF = {
    "spacing": "spacing",
    "padding": "spacing",
    "margin": "spacing",
    "alignment": "spacing",
    "font-size": "font",
    "font-weight": "font",
    "font-family": "font",
    "text-color": "color",
    "background": "color",
    "border": "color",
    "size": "size",
}


@dataclass
class Pair:
    d: Element
    v: Element
    sim: float = 1.0  # text similarity (1 for non-text)


# --------------------------------------------------------------------------- helpers


def _norm_text(t: str) -> str:
    t = unicodedata.normalize("NFC", t).lower()
    return re.sub(r"[\W_]+", "", t)


def _fmt(v: float, unit: str = "px") -> str:
    r = round(v)
    return f"{r}{unit}" if abs(v - r) < 0.25 else f"{v:.1f}{unit}"


def _signed(v: float, unit: str = "px") -> str:
    return ("+" if v > 0 else "") + _fmt(v, unit)


def estimate_font_size(text: str, ink_height: float) -> float:
    """Rough font size from the ink height of a text line (no font metrics available)."""
    top, bottom = 0.53, 0.0  # x-height / baseline, in em
    for ch in text:
        if not ch.strip():
            continue
        decomposed = unicodedata.normalize("NFD", ch)
        base, marks = decomposed[0], decomposed[1:]
        above = sum(1 for m in marks if m not in "\u0323\u031b")  # ignore dot below / horn
        if base in "gjpqyçµ,;":
            bottom = min(bottom, -0.2)
        elif "\u0323" in marks:  # Vietnamese dot below
            bottom = min(bottom, -0.12)
        if base.isupper():
            top = max(top, 0.72 + 0.2 * min(above, 2))
        elif base.isdigit() or base in "bdfhklt'\"!?/|()[]{}đ":
            top = max(top, 0.76)
        elif above:
            top = max(top, 0.76 + 0.18 * (above - 1))
    return ink_height / max(top - bottom, 0.4)


def _weight_text(w: Any) -> str:
    try:
        w = int(round(float(w) / 100) * 100)
    except (TypeError, ValueError):
        return str(w)
    return f"{w} ({WEIGHT_NAMES.get(w, '')})"


def _shape_similarity(a: np.ndarray | None, b: np.ndarray | None) -> float | None:
    """IoU of two glyph masks after resizing b onto a (1 = same glyph shapes)."""
    if a is None or b is None or a.size == 0 or b.size == 0:
        return None
    h, w = a.shape
    if h < 6 or w < 6:
        return None
    b2 = cv2.resize(b.astype(np.uint8) * 255, (w, h), interpolation=cv2.INTER_AREA) > 127
    k = np.ones((3, 3), np.uint8)
    # Tolerate 1px misalignment: compare each mask against the other one dilated.
    a_d = cv2.dilate(a.astype(np.uint8), k).astype(bool)
    b_d = cv2.dilate(b2.astype(np.uint8), k).astype(bool)
    inter = (a & b_d).sum() + (b2 & a_d).sum()
    total = a.sum() + b2.sum()
    return float(inter / total) if total else None


# --------------------------------------------------------------------------- matching


class Matcher:
    def __init__(self, des: Analysis, dev: Analysis):
        self.des, self.dev = des, dev
        self.scale = des.scale
        self.pairs: list[Pair] = []
        self.anchors: list[tuple[float, float, float]] = []  # (design cy, dx, dy)

    def shift(self, y: float) -> tuple[float, float]:
        if not self.anchors:
            return 0.0, 0.0
        near = sorted(self.anchors, key=lambda a: abs(a[0] - y))[:3]
        return median(a[1] for a in near), median(a[2] for a in near)

    def match_texts(self) -> None:
        dt, vt = self.des.of_kind("text"), self.dev.of_kind("text")
        nd = {e.id: _norm_text(e.text) for e in dt}
        nv = {e.id: _norm_text(e.text) for e in vt}
        sims: dict[tuple[str, str], float] = {}
        for d in dt:
            for v in vt:
                if not nd[d.id] or not nv[v.id]:
                    continue
                s = SequenceMatcher(None, nd[d.id], nv[v.id]).ratio()
                if s >= 0.5:
                    sims[(d.id, v.id)] = s

        # 1) anchors: texts that are unique on both sides and read the same
        strong = [(k, s) for k, s in sims.items() if s >= 0.9]
        d_count: dict[str, int] = {}
        v_count: dict[str, int] = {}
        for (di, vi), _ in strong:
            d_count[di] = d_count.get(di, 0) + 1
            v_count[vi] = v_count.get(vi, 0) + 1
        dmap, vmap = self.des.by_id(), self.dev.by_id()
        for (di, vi), _ in strong:
            if d_count[di] == 1 and v_count[vi] == 1:
                d, v = dmap[di], vmap[vi]
                self.anchors.append((d.rect.cy, v.rect.cx - d.rect.cx, v.rect.cy - d.rect.cy))

        # 2) greedy assignment on text similarity + distance from the expected position
        unit = 40 * self.scale
        cands = []
        for (di, vi), s in sims.items():
            d, v = dmap[di], vmap[vi]
            sdx, sdy = self.shift(d.rect.cy)
            dist = np.hypot(v.rect.cx - d.rect.cx - sdx, v.rect.cy - d.rect.cy - sdy) / unit
            cost = 2 * (1 - s) + dist
            if cost < 4:
                cands.append((cost, s, d, v))
        cands.sort(key=lambda c: c[0])
        used_d, used_v = set(), set()
        for _, s, d, v in cands:
            if d.id in used_d or v.id in used_v:
                continue
            used_d.add(d.id)
            used_v.add(v.id)
            self.pairs.append(Pair(d, v, s))

        # 3) texts whose content differs (dynamic data, OCR noise): pair by position
        for d in dt:
            if d.id in used_d:
                continue
            sdx, sdy = self.shift(d.rect.cy)
            p = d.rect.shifted(sdx, sdy)
            best, best_iou = None, 0.3
            for v in vt:
                if v.id in used_v or not 0.7 <= v.rect.h / max(d.rect.h, 1) <= 1.4:
                    continue
                iou = p.iou(v.rect)
                if iou > best_iou:
                    best, best_iou = v, iou
            if best is not None:
                used_d.add(d.id)
                used_v.add(best.id)
                self.pairs.append(Pair(d, best, sims.get((d.id, best.id), 0.0)))

        # refine anchors with every confident pair
        self.anchors = [
            (p.d.rect.cy, p.v.rect.cx - p.d.rect.cx, p.v.rect.cy - p.d.rect.cy) for p in self.pairs if p.sim >= 0.8
        ] or self.anchors

    def _match_kind(self, kind: str, min_score: float) -> None:
        d_els, v_els = self.des.of_kind(kind), self.dev.of_kind(kind)
        d2v = {p.d.id: p.v.id for p in self.pairs}
        cands = []
        for d in d_els:
            sdx, sdy = self.shift(d.rect.cy)
            pred = d.rect.shifted(sdx, sdy)
            d_children = {d2v[e.id] for e in self.des.elements if e.parent == d.id and e.id in d2v}
            for v in v_els:
                iou = pred.iou(v.rect)
                if kind == "graphic":
                    ratio_w = v.rect.w / max(d.rect.w, 1)
                    ratio_h = v.rect.h / max(d.rect.h, 1)
                    if not (0.5 <= ratio_w <= 2 and 0.5 <= ratio_h <= 2):
                        continue
                    near = np.hypot(pred.cx - v.rect.cx, pred.cy - v.rect.cy) <= 12 * self.scale
                    score = max(iou, 0.35 if near else 0.0)
                else:
                    v_children = {e.id for e in self.dev.elements if e.parent == v.id}
                    if d_children or v_children:
                        jac = len(d_children & v_children) / max(len(d_children | v_children), 1)
                        score = 0.5 * iou + 0.5 * jac
                    else:
                        score = iou
                if score >= min_score:
                    cands.append((score, d, v))
        cands.sort(key=lambda c: -c[0])
        used_d, used_v = set(), set()
        for _, d, v in cands:
            if d.id in used_d or v.id in used_v:
                continue
            used_d.add(d.id)
            used_v.add(v.id)
            self.pairs.append(Pair(d, v))

    def run(self) -> list[Pair]:
        self.match_texts()
        # Box scores use the texts matched above (shared children), so texts go first.
        self._match_kind("box", 0.4)
        self._match_kind("graphic", 0.3)
        return self.pairs


# --------------------------------------------------------------------------- comparison


class Comparer:
    def __init__(self, des: Analysis, dev: Analysis, opts: Options):
        self.des, self.dev, self.opts = des, dev, opts
        self.s = des.scale
        self.issues: list[dict[str, Any]] = []
        self.pairs = Matcher(des, dev).run()
        self.d2v = {p.d.id: p.v for p in self.pairs}
        self.dmap = des.by_id()
        self.same_width = abs(des.width - dev.width) <= 2 * self.s
        self.low_res = min(des.source_scale, dev.source_scale) < 1.5
        self.lossy = des.lossy or dev.lossy
        # Extra tolerance when the inputs are blurry (1x) or compressed (JPEG).
        self.extra_color = (3 if self.low_res else 0) + (2 if self.lossy else 0)
        self.weight_tol = 0.12 if (self.low_res or self.lossy) else 0.08

    # -- utils
    def px(self, v: float) -> float:
        return v / self.s

    def enabled(self, check: str) -> bool:
        return check in self.opts.checks

    def parent_ok(self, d: Element, v: Element) -> bool:
        """The dev element sits in the counterpart of the design element's parent."""
        if d.parent is None:
            return v.parent is None
        pv = self.d2v.get(d.parent)
        return pv is not None and pv.id == v.parent

    def add(
        self,
        category: str,
        label: str,
        expected: str,
        actual: str,
        delta: str,
        major: bool,
        message: str,
        d_marks: list[tuple[Rect, str]],
        v_marks: list[tuple[Rect, str]],
        elements: tuple[str, ...] = (),
    ) -> None:
        self.issues.append(
            {
                "category": category,
                "group": GROUP_OF[category],
                "severity": "major" if major else "minor",
                "label": label,
                "expected": expected,
                "actual": actual,
                "delta": delta,
                "message": message,
                "elements": list(elements),
                "design_marks": [{"rect": r.to_list(), "type": t} for r, t in d_marks],
                "dev_marks": [{"rect": r.to_list(), "type": t} for r, t in v_marks],
            }
        )

    def check_distance(
        self, category: str, label: str, d_val: float, v_val: float, message: str,
        d_marks: list, v_marks: list, elements: tuple[str, ...] = (),
    ) -> None:
        de, ve = self.px(d_val), self.px(v_val)
        diff = ve - de
        tol = self.opts.tol_spacing
        if abs(diff) <= tol:
            return
        self.add(
            category, label, _fmt(de), _fmt(ve), _signed(diff), abs(diff) >= max(2 * tol, 4),
            message, d_marks, v_marks, elements,
        )

    # -- text
    def check_text(self, p: Pair) -> None:
        d, v = p.d, p.v
        fig = d.figma or {}
        marks_d, marks_v = [(d.rect, "el")], [(v.rect, "el")]
        ids = (d.id, v.id)

        if self.enabled("color") and d.color and v.color:
            de = delta_e(d.color, v.color)
            tol = self.opts.tol_color + self.extra_color
            if de > tol:
                expected = fig.get("color") if fig.get("color") else to_hex(d.color)
                self.add(
                    "text-color", f"Màu chữ {d.label()}", expected, to_hex(v.color), f"ΔE {de:.1f}",
                    de >= 2 * tol, "Màu chữ khác design.", marks_d, marks_v, ids,
                )

        if not self.enabled("font") or p.sim < 0.9:
            return
        hr = v.rect.h / max(d.rect.h, 1)
        des_size = float(fig["fontSize"]) if fig.get("fontSize") else estimate_font_size(d.text, self.px(d.rect.h))
        dev_size = des_size * hr
        approx = "" if fig.get("fontSize") else "≈"
        tol = self.opts.tol_font_size + (0.5 if self.low_res else 0)
        size_bad = abs(dev_size - des_size) > tol and abs(hr - 1) > 0.04
        if size_bad:
            self.add(
                "font-size", f"Cỡ chữ {d.label()}", f"{approx}{_fmt(des_size)}", f"≈{_fmt(dev_size)}",
                _signed(dev_size - des_size), abs(dev_size - des_size) >= 2,
                f"Chiều cao chữ {_fmt(self.px(d.rect.h))} → {_fmt(self.px(v.rect.h))}.",
                marks_d, marks_v, ids,
            )

        # Stroke thickness relative to text height, and width at equal font size.
        rs = (v.stroke / max(v.rect.h, 1)) / max(d.stroke / max(d.rect.h, 1), 1e-6)
        wr = (v.rect.w / max(d.rect.w, 1)) / hr
        weight_off = abs(rs - 1) > self.weight_tol
        width_off = abs(wr - 1) > 0.06 and abs(self.px(v.rect.w - d.rect.w)) > 2
        family = f"{fig['fontFamily']} " if fig.get("fontFamily") else ""
        expected_font = f"{family}{_weight_text(fig['fontWeight'])}".strip() if fig.get("fontWeight") else family.strip()
        shape = _shape_similarity(d.mask, v.mask) if p.sim >= 0.95 and not size_bad else None

        # A heavier weight makes glyphs both thicker and wider; another family usually
        # changes width independently of (or opposite to) stroke thickness.
        if p.sim >= 0.95 and not size_bad and width_off and (not weight_off or (wr - 1) * (rs - 1) < 0):
            self.add(
                "font-family", f"Font chữ {d.label()}",
                expected_font or f"chữ rộng {_fmt(self.px(d.rect.w))}",
                f"chữ rộng {_fmt(self.px(v.rect.w))} ({(wr - 1) * 100:+.0f}%)",
                _signed(self.px(v.rect.w - d.rect.w)), abs(wr - 1) > 0.1,
                "Cùng nội dung, cùng cỡ chữ nhưng chiều rộng chữ khác → sai font-family hoặc letter-spacing."
                + (" Nét chữ cũng " + ("đậm" if rs > 1 else "mảnh") + f" hơn {abs(rs - 1) * 100:.0f}%." if weight_off else ""),
                marks_d, marks_v, ids,
            )
        elif shape is not None and shape < 0.85:
            self.add(
                "font-family", f"Font chữ {d.label()}", expected_font or "hình dạng chữ như design", "hình dạng chữ khác",
                f"giống {shape * 100:.0f}%", shape < 0.75,
                "Hình dạng ký tự khác design → kiểm tra font-family.", marks_d, marks_v, ids,
            )
        elif weight_off:
            stroke_d = self.px(d.stroke)
            stroke_v = self.px(v.stroke) / hr  # at the design font size
            expected = f"nét {stroke_d:.1f}px"
            if fig.get("fontWeight"):
                expected = f"{_weight_text(fig['fontWeight'])}, {expected}"
            self.add(
                "font-weight", f"Độ đậm chữ {d.label()}", expected,
                f"nét {stroke_v:.1f}px ({'đậm' if rs > 1 else 'mảnh'} hơn)", f"{(rs - 1) * 100:+.0f}%", abs(rs - 1) >= 0.2,
                "Độ dày nét chữ khác design → kiểm tra font-weight.", marks_d, marks_v, ids,
            )

    # -- boxes
    def check_box(self, p: Pair) -> None:
        d, v = p.d, p.v
        fig = d.figma or {}
        marks_d, marks_v = [(d.rect, "el")], [(v.rect, "el")]
        ids = (d.id, v.id)
        if self.enabled("color"):
            de = delta_e(d.color, v.color)
            if de > self.opts.tol_color:
                self.add(
                    "background", f"Màu nền {d.label()}", fig.get("fill") or to_hex(d.color), to_hex(v.color),
                    f"ΔE {de:.1f}", de >= 2 * self.opts.tol_color, "Màu nền khác design.", marks_d, marks_v, ids,
                )
            if d.border_color and v.border_color:
                de = delta_e(d.border_color, v.border_color)
                if de > self.opts.tol_color + self.extra_color:
                    self.add(
                        "border", f"Màu viền {d.label()}", to_hex(d.border_color), to_hex(v.border_color),
                        f"ΔE {de:.1f}", de >= 2 * self.opts.tol_color, "Màu viền khác design.", marks_d, marks_v, ids,
                    )
            elif bool(d.border_color) != bool(v.border_color):
                self.add(
                    "border", f"Viền {d.label()}",
                    f"viền {to_hex(d.border_color)}" if d.border_color else "không viền",
                    f"viền {to_hex(v.border_color)}" if v.border_color else "không viền",
                    "—", True, "Design và bản code khác nhau về việc có viền.", marks_d, marks_v, ids,
                )
        if self.enabled("size"):
            self.check_distance("size", f"Chiều cao {d.label()}", d.rect.h, v.rect.h,
                                "Chiều cao khối khác design.", marks_d, marks_v, ids)
            fixed_width = d.rect.w < 0.7 * self.des.width
            if (self.same_width or fixed_width) and not self.width_explained(d, v):
                self.check_distance("size", f"Chiều rộng {d.label()}", d.rect.w, v.rect.w,
                                    "Chiều rộng khối khác design.", marks_d, marks_v, ids)

    def width_explained(self, d: Element, v: Element) -> bool:
        """True when a width change only follows its container / inset changes."""
        if d.parent is None:
            pd_rect = Rect(0, 0, self.des.width, self.des.height)
            pv_rect = Rect(0, 0, self.dev.width, self.dev.height)
        else:
            pv = self.d2v.get(d.parent)
            if pv is None or pv.id != v.parent:
                return False
            pd_rect, pv_rect = self.dmap[d.parent].rect, pv.rect
        d_left, d_right = d.rect.x - pd_rect.x, pd_rect.right - d.rect.right
        v_left, v_right = v.rect.x - pv_rect.x, pv_rect.right - v.rect.right
        expected = d.rect.w + (pv_rect.w - pd_rect.w) - (v_left - d_left) - (v_right - d_right)
        changed_inset = abs(v_left - d_left) > self.opts.tol_spacing * self.s or abs(v_right - d_right) > self.opts.tol_spacing * self.s
        changed_parent = abs(pv_rect.w - pd_rect.w) > self.opts.tol_spacing * self.s
        return (changed_inset or changed_parent) and abs(v.rect.w - expected) <= self.opts.tol_spacing * self.s

    def check_padding(self, p: Pair) -> None:
        d, v = p.d, p.v
        kids = [
            (c, self.d2v[c.id])
            for c in self.des.elements
            if c.parent == d.id and c.id in self.d2v and self.d2v[c.id].parent == v.id
        ]
        if not kids:
            return
        cd = Rect.union([k[0].rect for k in kids])
        cv = Rect.union([k[1].rect for k in kids])
        pd = {"top": cd.y - d.rect.y, "left": cd.x - d.rect.x, "right": d.rect.right - cd.right, "bottom": d.rect.bottom - cd.bottom}
        pv = {"top": cv.y - v.rect.y, "left": cv.x - v.rect.x, "right": v.rect.right - cv.right, "bottom": v.rect.bottom - cv.bottom}
        tol_px = self.opts.tol_spacing * self.s
        # Text/icons that got wider or taller (font issues, reported separately) shift the
        # right/bottom edge of the content, so those paddings are not meaningful then.
        content_w_same = all(abs(a.rect.w - b.rect.w) <= tol_px for a, b in kids if a.kind != "box")
        content_h_same = all(abs(a.rect.h - b.rect.h) <= tol_px for a, b in kids if a.kind != "box")
        names = {"top": "trên", "left": "trái", "right": "phải", "bottom": "dưới"}

        def gap_rect(r: Rect, c: Rect, side: str) -> Rect:
            if side == "top":
                return Rect(c.x, r.y, c.w, c.y - r.y)
            if side == "bottom":
                return Rect(c.x, c.bottom, c.w, r.bottom - c.bottom)
            if side == "left":
                return Rect(r.x, c.y, c.x - r.x, c.h)
            return Rect(c.right, c.y, r.right - c.right, c.h)

        h_centered = abs(pd["left"] - pd["right"]) <= 2 * self.s and len(kids) <= 2
        for side in ("top", "left", "right", "bottom"):
            if side == "right" and not content_w_same:
                continue
            if side == "bottom" and not content_h_same:
                continue
            if side in ("left", "right") and h_centered:
                continue  # centered content: checked as alignment below
            self.check_distance(
                "padding", f"Padding-{side} của {d.label()}", pd[side], pv[side],
                f"Khoảng cách từ mép {names[side]} của khối tới nội dung bên trong khác design.",
                [(d.rect, "el"), (gap_rect(d.rect, cd, side), "gap")],
                [(v.rect, "el"), (gap_rect(v.rect, cv, side), "gap")],
                (d.id, v.id),
            )
        if h_centered:
            off = abs(pv["left"] - pv["right"])
            if self.px(off) > 2 * self.opts.tol_spacing:
                self.add(
                    "alignment", f"Căn giữa trong {d.label()}", "căn giữa", f"lệch {_fmt(self.px(off) / 2)}",
                    _fmt(self.px(off) / 2), True, "Nội dung được căn giữa trong design nhưng bị lệch trong bản code.",
                    [(d.rect, "el"), (cd, "gap")], [(v.rect, "el"), (cv, "gap")], (d.id, v.id),
                )

    # -- screen margins (elements not inside any box)
    def check_margins(self) -> None:
        W_d, W_v = self.des.width, self.dev.width
        roots = [p for p in self.pairs if p.d.parent is None and p.v.parent is None]

        def has_neighbor(p: Pair, side: str) -> bool:
            """Another top-level element on the same row, on that side (the inset then
            depends on that element and its gap, which are checked as spacing)."""
            r = p.d.rect
            for q in roots:
                o = q.d.rect
                if q is p or r.v_overlap(o) < 0.3 * min(r.h, o.h):
                    continue
                if (side == "left" and o.right <= r.x + self.s) or (side == "right" and o.x >= r.right - self.s):
                    return True
            return False

        for p in roots:
            d, v = p.d, p.v
            left_d, right_d = d.rect.x, W_d - d.rect.right
            left_v, right_v = v.rect.x, W_v - v.rect.right
            centered = abs(d.rect.cx - W_d / 2) <= 2 * self.s and min(left_d, right_d) > 8 * self.s
            if centered and not self.same_width:
                # centered on screen: the dev element must be centered too
                off = v.rect.cx - W_v / 2
                if abs(self.px(off)) > self.opts.tol_spacing:
                    self.add(
                        "alignment", f"Căn giữa màn hình {d.label()}", "căn giữa", f"lệch {_signed(self.px(off))}",
                        _signed(self.px(off)), abs(self.px(off)) >= 4, "Phần tử được căn giữa màn hình trong design.",
                        [(d.rect, "el")], [(v.rect, "el")], (d.id, v.id),
                    )
                continue
            sides = []
            if left_d <= right_d or (left_d < 0.15 * W_d and right_d < 0.15 * W_d):
                sides.append(("left", left_d, left_v, Rect(0, d.rect.y, left_d, d.rect.h), Rect(0, v.rect.y, left_v, v.rect.h)))
            if right_d < left_d or (left_d < 0.15 * W_d and right_d < 0.15 * W_d):
                sides.append((
                    "right", right_d, right_v,
                    Rect(d.rect.right, d.rect.y, right_d, d.rect.h), Rect(v.rect.right, v.rect.y, right_v, v.rect.h),
                ))
            for side, a, b, gd, gv in sides:
                if has_neighbor(p, side):
                    continue
                self.check_distance(
                    "margin", f"Lề {'trái' if side == 'left' else 'phải'} màn hình của {d.label()}", a, b,
                    "Khoảng cách tới mép màn hình khác design.",
                    [(d.rect, "el"), (gd, "gap")], [(v.rect, "el"), (gv, "gap")], (d.id, v.id),
                )

    # -- spacing between siblings
    def check_spacing(self) -> None:
        by_parent: dict[str | None, list[Pair]] = {}
        for p in self.pairs:
            if self.parent_ok(p.d, p.v):
                by_parent.setdefault(p.d.parent, []).append(p)
        slack = 1 * self.s
        seen: set[tuple[str, str]] = set()
        for group in by_parent.values():
            for a in group:
                below = right = None
                for b in group:
                    if b is a:
                        continue
                    ra, rb = a.d.rect, b.d.rect
                    if rb.y >= ra.bottom - slack and ra.h_overlap(rb) > 0.3 * min(ra.w, rb.w):
                        if below is None or rb.y < below.d.rect.y:
                            below = b
                    if rb.x >= ra.right - slack and ra.v_overlap(rb) > 0.3 * min(ra.h, rb.h):
                        if right is None or rb.x < right.d.rect.x:
                            right = b
                for b, vertical in ((below, True), (right, False)):
                    if b is None or (a.d.id, b.d.id) in seen:
                        continue
                    seen.add((a.d.id, b.d.id))
                    self._spacing_pair(a, b, vertical)

    def _spacing_pair(self, a: Pair, b: Pair, vertical: bool) -> None:
        def gap(r1: Rect, r2: Rect) -> tuple[float, Rect]:
            if vertical:
                x0 = max(r1.x, r2.x)
                x1 = min(r1.right, r2.right)
                return r2.y - r1.bottom, Rect(x0, r1.bottom, max(x1 - x0, 2), r2.y - r1.bottom)
            y0 = max(r1.y, r2.y)
            y1 = min(r1.bottom, r2.bottom)
            return r2.x - r1.right, Rect(r1.right, y0, r2.x - r1.right, max(y1 - y0, 2))

        gd, rd = gap(a.d.rect, b.d.rect)
        gv, rv = gap(a.v.rect, b.v.rect)
        if vertical and gv < -self.s:  # order changed in the dev build; not a spacing issue
            return
        same_para = (
            vertical
            and a.d.kind == b.d.kind == "text"
            and abs(a.d.rect.x - b.d.rect.x) <= 2 * self.s
            and gd < 0.8 * a.d.rect.h
        )
        if same_para:
            label = f"Khoảng cách dòng giữa {a.d.label(20)} và {b.d.label(20)}"
            message = "Khoảng cách giữa các dòng trong cùng đoạn khác design → kiểm tra line-height."
        else:
            label = f"Khoảng cách {'dọc' if vertical else 'ngang'} giữa {a.d.label(20)} và {b.d.label(20)}"
            message = "Khoảng cách (margin/gap) giữa hai phần tử khác design."
        self.check_distance(
            "spacing", label, gd, gv, message,
            [(a.d.rect, "el"), (b.d.rect, "el"), (rd, "gap")],
            [(a.v.rect, "el"), (b.v.rect, "el"), (rv, "gap")],
            (a.d.id, b.d.id, a.v.id, b.v.id),
        )

    # -- main
    def run(self) -> dict[str, Any]:
        for p in self.pairs:
            if p.d.kind == "text":
                self.check_text(p)
            elif p.d.kind == "box":
                self.check_box(p)
                if self.enabled("padding"):
                    self.check_padding(p)
        if self.enabled("padding"):
            self.check_margins()
        if self.enabled("spacing"):
            self.check_spacing()

        order = {"major": 0, "minor": 1}
        self.issues.sort(key=lambda i: (order[i["severity"]], i["design_marks"][0]["rect"][1] if i["design_marks"] else 0))
        for n, issue in enumerate(self.issues, 1):
            issue["id"] = n

        groups: dict[str, int] = {"spacing": 0, "font": 0, "color": 0, "size": 0}
        for i in self.issues:
            groups[i["group"]] += 1
        matched_ids = {p.d.id for p in self.pairs}
        unmatched = [e for e in self.des.elements if e.id not in matched_ids and e.kind == "text"]
        return {
            "issues": self.issues,
            "summary": {
                "total": len(self.issues),
                "major": sum(1 for i in self.issues if i["severity"] == "major"),
                "minor": sum(1 for i in self.issues if i["severity"] == "minor"),
                "by_group": groups,
                "matched": len(self.pairs),
                "design_elements": len(self.des.elements),
                "dev_elements": len(self.dev.elements),
            },
            "pairs": [{"design": p.d.id, "dev": p.v.id, "kind": p.d.kind} for p in self.pairs],
            "unmatched_design_texts": [{"id": e.id, "text": e.text, "rect": e.rect.to_list()} for e in unmatched],
        }


def compare(des: Analysis, dev: Analysis, opts: Options) -> dict[str, Any]:
    return Comparer(des, dev, opts).run()
