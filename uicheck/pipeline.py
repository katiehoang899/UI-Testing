"""End-to-end check: design (image or Figma link) vs dev screenshot."""

from __future__ import annotations

from typing import Any

from . import figma
from .compare import Comparer
from .detect import analyze
from .imaging import analysis_scale, crop_vertical, encode_image, guess_scale, is_lossy, load_image, normalize
from .models import Options


def run(
    dev_bytes: bytes,
    design_bytes: bytes | None = None,
    opts: Options | None = None,
    figma_url: str | None = None,
    figma_token: str | None = None,
    include_images: bool = True,
) -> dict[str, Any]:
    opts = opts or Options()
    specs = None
    design_scale = opts.design_scale
    if figma_url:
        design_bytes, design_scale, specs = figma.fetch(figma_url, figma_token)
    if design_bytes is None:
        raise ValueError("Cần ảnh design hoặc link Figma.")

    des_img = load_image(design_bytes)
    dev_img = load_image(dev_bytes)
    ds = design_scale or guess_scale(des_img.shape[1])
    vs = opts.dev_scale or guess_scale(dev_img.shape[1])
    des_width = des_img.shape[1] / ds
    dev_width = dev_img.shape[1] / vs
    a = analysis_scale(des_width)

    des_n = crop_vertical(normalize(des_img, ds, a), opts.ignore_top * a, opts.ignore_bottom * a)
    dev_n = crop_vertical(normalize(dev_img, vs, a), opts.ignore_top * a, opts.ignore_bottom * a)

    des = analyze(des_n, a, ds, opts.lang)
    dev = analyze(dev_n, a, vs, opts.lang)
    des.lossy, dev.lossy = is_lossy(design_bytes), is_lossy(dev_bytes)
    if specs:
        figma.annotate(des, specs, opts.ignore_top)

    comparer = Comparer(des, dev, opts)
    result = comparer.run()

    # Overlay alignment: line up the top-most confidently matched text, so that drift
    # further down the screen stays visible.
    texts = sorted((p for p in comparer.pairs if p.d.kind == "text" and p.sim >= 0.8), key=lambda p: p.d.rect.y)
    top = texts[0] if texts else (comparer.pairs[0] if comparer.pairs else None)
    dx = top.v.rect.x - top.d.rect.x if top else 0.0
    dy = top.v.rect.y - top.d.rect.y if top else 0.0

    warnings = []
    if abs(des_width - dev_width) > 2:
        warnings.append(
            f"Chiều rộng màn hình khác nhau (design {des_width:.0f}px, dev {dev_width:.0f}px): "
            "chiều rộng các khối co giãn sẽ không được so sánh. Kiểm tra lại scale nếu không đúng."
        )
    if not result["pairs"]:
        warnings.append("Không ghép được phần tử nào giữa 2 ảnh — kiểm tra lại có đúng cùng một màn hình và scale không.")
    elif len([p for p in result["pairs"] if p["kind"] == "text"]) < 0.4 * max(len(des.of_kind("text")), 1):
        warnings.append("Chỉ ghép được ít text giữa 2 ảnh, kết quả có thể thiếu chính xác.")
    if des.lossy or dev.lossy:
        warnings.append("Ảnh JPEG làm sai lệch màu và nét chữ: ngưỡng màu/độ đậm đã được nới. Nên dùng PNG.")
    if min(ds, vs) < 1.5:
        warnings.append("Có ảnh độ phân giải 1x: nên export design / chụp màn hình ở 2x-3x để đo màu và font chính xác hơn.")

    result.update(
        {
            "analysis_scale": a,
            "design": {
                "source": "figma" if specs else "upload",
                "scale": ds,
                "width": round(des_width, 1),
                "size": [des.width, des.height],
                "background": "#%02X%02X%02X" % des.background,
            },
            "dev": {
                "scale": vs,
                "width": round(dev_width, 1),
                "size": [dev.width, dev.height],
                "background": "#%02X%02X%02X" % dev.background,
            },
            "alignment": {"dx": round(dx, 1), "dy": round(dy, 1)},
            "elements": {
                "design": [e.to_dict(a) for e in des.elements],
                "dev": [e.to_dict(a) for e in dev.elements],
            },
            "warnings": warnings,
            "options": {
                "tol_spacing": opts.tol_spacing,
                "tol_font_size": opts.tol_font_size,
                "tol_color": opts.tol_color,
                "checks": list(opts.checks),
            },
        }
    )
    if specs:
        result["figma"] = {k: specs.get(k) for k in ("name", "file_key", "node_id", "width", "height")}
    if include_images:
        result["design"]["image"] = encode_image(des_n)
        result["dev"]["image"] = encode_image(dev_n)
    return result
