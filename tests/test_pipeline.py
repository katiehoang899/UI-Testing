"""End-to-end tests on synthetic screens with known, injected differences."""

import io
import shutil
from functools import lru_cache

import pytest

from PIL import Image

from synth import font_available, render
from uicheck import Options, run

pytestmark = pytest.mark.skipif(
    shutil.which("tesseract") is None or not font_available(), reason="needs tesseract and test fonts"
)


@lru_cache(maxsize=None)
def _design() -> bytes:
    return render()


def check(**dev_overrides):
    return run(render(**dev_overrides), _design(), Options(), include_images=False)


def categories(result):
    return sorted({i["category"] for i in result["issues"]})


@pytest.mark.parametrize("scale", [3, 2, 1])
def test_identical_screen_has_no_issue(scale):
    r = check(scale=scale)
    assert r["issues"] == []
    assert r["summary"]["matched"] >= 8


def test_wider_device_without_changes_has_no_issue():
    r = check(width=390)
    assert r["issues"] == []
    assert any("Chiều rộng màn hình" in w for w in r["warnings"])


def test_card_padding():
    r = check(card_padding=24)
    pads = {i["label"].split(" ")[0]: i for i in r["issues"] if i["category"] == "padding"}
    assert set(pads) == {"Padding-top", "Padding-left", "Padding-right", "Padding-bottom"}
    assert all(i["delta"] == "+8px" for i in pads.values())
    # the inputs only stretch with the card: not reported as their own width issue
    assert not any(i["category"] == "size" and "Chiều rộng" in i["label"] for i in r["issues"])


def test_vertical_spacing():
    r = check(gap_subtitle_card=32)
    spacing = [i for i in r["issues"] if i["category"] == "spacing"]
    assert len(spacing) == 1
    assert spacing[0]["delta"] == "+8px"
    assert "Chào mừng" in spacing[0]["label"]


def test_screen_margin():
    r = check(margin=20)
    margins = [i for i in r["issues"] if i["category"] == "margin"]
    assert margins and all(i["delta"] == "+4px" for i in margins)
    assert not any(i["category"] == "size" for i in r["issues"])


def test_font_size():
    r = check(title_size=20)
    assert categories(r) == ["font-size"]
    issue = r["issues"][0]
    assert "Đăng nhập" in issue["label"]
    assert issue["delta"].startswith("-")


def test_font_weight():
    r = check(label_weight=400)
    weights = [i for i in r["issues"] if i["category"] == "font-weight"]
    assert len(weights) == 2
    assert all("mảnh hơn" in i["actual"] for i in weights)


def test_font_family():
    r = check(serif_label=True)
    assert categories(r) == ["font-family"]
    assert len(r["issues"]) == 2


def test_colors():
    r = check(button_bg="#6366F1", subtitle_color="#9CA3AF")
    by_cat = {i["category"]: i for i in r["issues"]}
    assert set(by_cat) == {"background", "text-color"}
    assert by_cat["background"]["expected"] == "#4F46E5"
    assert by_cat["background"]["actual"] == "#6366F1"
    assert by_cat["text-color"]["actual"] == "#9CA3AF"


def test_border_color():
    r = check(input_border="#9CA3AF")
    assert categories(r) == ["border"]
    assert len(r["issues"]) == 2


def test_button_height():
    r = check(button_height=44)
    sizes = [i for i in r["issues"] if i["category"] == "size"]
    assert len(sizes) == 1 and sizes[0]["delta"] == "-4px"


def test_checks_can_be_disabled():
    r = run(render(button_bg="#6366F1", card_padding=24), _design(), Options(checks=("font",)), include_images=False)
    assert r["issues"] == []


def _jpeg(png: bytes, quality: int = 85) -> bytes:
    buf = io.BytesIO()
    Image.open(io.BytesIO(png)).save(buf, "JPEG", quality=quality)
    return buf.getvalue()


@pytest.mark.parametrize("scale", [3, 2])
def test_jpeg_screenshot_without_changes_has_no_issue(scale):
    r = run(_jpeg(render(scale=scale)), _design(), Options(), include_images=False)
    assert r["issues"] == []
    assert any("JPEG" in w for w in r["warnings"])


def test_jpeg_screenshot_still_reports_padding():
    r = run(_jpeg(render(scale=2, card_padding=24)), _design(), Options(), include_images=False)
    assert {i["label"].split(" ")[0] for i in r["issues"] if i["category"] == "padding"} == {
        "Padding-top", "Padding-left", "Padding-right", "Padding-bottom",
    }


def test_icon_to_text_gap():
    design = render(icon=True)
    same = run(render(icon=True, scale=2), design, Options(), include_images=False)
    assert same["issues"] == []
    r = run(render(icon=True, icon_gap=20), design, Options(), include_images=False)
    spacing = [i for i in r["issues"] if i["category"] == "spacing"]
    assert len(spacing) == 1
    assert "ngang" in spacing[0]["label"] and "Xin chào" in spacing[0]["label"]
    assert spacing[0]["delta"] == "+8px"
