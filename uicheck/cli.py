"""Command line: python -m uicheck --dev dev.png --design design.png [--json report.json]

Exit code: 0 = no issue, 1 = only minor issues, 2 = major issues (handy in CI).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import pipeline
from .figma import FigmaError
from .models import Options

GROUP_NAMES = {"spacing": "Khoảng cách", "font": "Font", "color": "Màu", "size": "Kích thước"}


def _scale(v: str) -> float | None:
    return None if v == "auto" else float(v)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="uicheck", description="So sánh screenshot bản code với design (spacing, font, màu).")
    ap.add_argument("--dev", required=True, help="Screenshot bản đã code")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--design", help="Ảnh design (PNG/JPG)")
    src.add_argument("--figma", help="Link Figma tới frame (cần token)")
    ap.add_argument("--token", help="Figma personal access token (mặc định: biến môi trường FIGMA_TOKEN)")
    ap.add_argument("--design-scale", default="auto", type=_scale, help="Pixel ratio của ảnh design (auto, 1, 2, 3...)")
    ap.add_argument("--dev-scale", default="auto", type=_scale, help="Pixel ratio của screenshot (auto, 1, 2, 3...)")
    ap.add_argument("--tol-spacing", type=float, default=2.0, help="Sai số khoảng cách cho phép (px)")
    ap.add_argument("--tol-font", type=float, default=1.0, help="Sai số cỡ chữ cho phép (px)")
    ap.add_argument("--tol-color", type=float, default=3.0, help="Sai số màu cho phép (ΔE2000)")
    ap.add_argument("--checks", default="spacing,padding,font,color,size", help="Các nhóm kiểm tra, phân tách bằng dấu phẩy")
    ap.add_argument("--ignore-top", type=float, default=0.0, help="Bỏ qua N px phía trên (status bar)")
    ap.add_argument("--ignore-bottom", type=float, default=0.0, help="Bỏ qua N px phía dưới")
    ap.add_argument("--lang", default="vie+eng", help="Ngôn ngữ OCR (vie+eng, eng)")
    ap.add_argument("--json", dest="json_out", help="Ghi báo cáo JSON ra file")
    args = ap.parse_args(argv)

    opts = Options(
        tol_spacing=args.tol_spacing,
        tol_font_size=args.tol_font,
        tol_color=args.tol_color,
        checks=tuple(c.strip() for c in args.checks.split(",") if c.strip()),
        lang=args.lang,
        design_scale=args.design_scale,
        dev_scale=args.dev_scale,
        ignore_top=args.ignore_top,
        ignore_bottom=args.ignore_bottom,
    )
    try:
        result = pipeline.run(
            Path(args.dev).read_bytes(),
            Path(args.design).read_bytes() if args.design else None,
            opts,
            figma_url=args.figma,
            figma_token=args.token,
            include_images=False,
        )
    except (FigmaError, ValueError, OSError) as e:
        print(f"Lỗi: {e}", file=sys.stderr)
        return 3

    s = result["summary"]
    print(f"Design {result['design']['width']}px @{result['design']['scale']}x  |  Dev {result['dev']['width']}px @{result['dev']['scale']}x")
    print(f"Đã đối chiếu {s['matched']} phần tử. Phát hiện {s['total']} vấn đề ({s['major']} nghiêm trọng).")
    print("  " + "  ".join(f"{GROUP_NAMES[g]}: {n}" for g, n in s["by_group"].items()))
    for w in result["warnings"]:
        print(f"! {w}")
    for i in result["issues"]:
        sev = "MAJOR" if i["severity"] == "major" else "minor"
        print(f"{i['id']:>3}. [{sev}] {i['label']}: design {i['expected']} → code {i['actual']} ({i['delta']})")

    if args.json_out:
        Path(args.json_out).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Đã ghi báo cáo: {args.json_out}")
    if s["major"]:
        return 2
    return 1 if s["total"] else 0


if __name__ == "__main__":
    sys.exit(main())
