import pytest

from uicheck import figma
from uicheck.color import delta_e
from uicheck.compare import estimate_font_size
from uicheck.imaging import guess_scale
from uicheck.models import Analysis, Element, Rect

import numpy as np


def test_delta_e_reference_values():
    assert delta_e((255, 255, 255), (255, 255, 255)) == pytest.approx(0)
    assert delta_e((0, 0, 0), (255, 255, 255)) == pytest.approx(100, abs=0.01)
    # Sharma et al. CIEDE2000 test pair #1 is in Lab; check a perceptible sRGB pair instead
    assert 1 < delta_e((79, 70, 229), (99, 102, 241)) < 15


@pytest.mark.parametrize(
    "width,expected",
    [(375, 1), (750, 2), (1125, 3), (1170, 3), (1290, 3), (828, 2), (1440, 1), (2880, 2), (1080, 3)],
)
def test_guess_scale(width, expected):
    assert guess_scale(width) == expected


def test_estimate_font_size():
    assert estimate_font_size("Email", 10.7) == pytest.approx(14, abs=0.5)
    assert estimate_font_size("Đăng nhập", 23.7) == pytest.approx(24, abs=1.2)


@pytest.mark.parametrize(
    "url,key,node",
    [
        ("https://www.figma.com/design/AbC123xyz/My-App?node-id=12-345&t=x", "AbC123xyz", "12:345"),
        ("https://www.figma.com/file/AbC123xyz/My-App?node-id=12%3A345", "AbC123xyz", "12:345"),
        ("https://www.figma.com/proto/AbC123xyz/My-App?node-id=1-2&scaling=min-zoom", "AbC123xyz", "1:2"),
        ("https://www.figma.com/design/AbC123xyz/branch/BrAnCh9/My-App?node-id=3-4", "BrAnCh9", "3:4"),
        ("https://figma.com/design/AbC123xyz/My-App", "AbC123xyz", None),
    ],
)
def test_parse_figma_url(url, key, node):
    assert figma.parse_url(url) == (key, node)


def test_parse_figma_url_rejects_other_hosts():
    with pytest.raises(figma.FigmaError):
        figma.parse_url("https://example.com/design/abc")


FRAME = {
    "id": "1:2",
    "name": "Login",
    "type": "FRAME",
    "absoluteBoundingBox": {"x": 100, "y": 200, "width": 375, "height": 640},
    "fills": [{"type": "SOLID", "color": {"r": 0.96, "g": 0.965, "b": 0.98, "a": 1}}],
    "children": [
        {
            "id": "1:3",
            "name": "Title",
            "type": "TEXT",
            "characters": "Đăng nhập",
            "absoluteBoundingBox": {"x": 116, "y": 240, "width": 125, "height": 29},
            "style": {"fontFamily": "Inter", "fontWeight": 700, "fontSize": 24, "lineHeightPx": 29},
            "fills": [{"type": "SOLID", "color": {"r": 0.102, "g": 0.102, "b": 0.18, "a": 1}}],
        },
        {
            "id": "1:4",
            "name": "Button",
            "type": "INSTANCE",
            "layoutMode": "HORIZONTAL",
            "paddingTop": 14, "paddingBottom": 14, "paddingLeft": 24, "paddingRight": 24, "itemSpacing": 8,
            "absoluteBoundingBox": {"x": 116, "y": 529, "width": 343, "height": 48},
            "fills": [{"type": "SOLID", "color": {"r": 0.31, "g": 0.275, "b": 0.898, "a": 1}}],
            "children": [],
        },
        {
            "id": "1:5",
            "name": "Hidden",
            "type": "RECTANGLE",
            "visible": False,
            "absoluteBoundingBox": {"x": 0, "y": 0, "width": 10, "height": 10},
            "fills": [{"type": "SOLID", "color": {"r": 1, "g": 0, "b": 0, "a": 1}}],
        },
    ],
}


def test_extract_specs():
    specs = figma.extract_specs(FRAME)
    assert specs["width"] == 375
    assert [t["characters"] for t in specs["texts"]] == ["Đăng nhập"]
    t = specs["texts"][0]
    assert t["rect"] == [16, 40, 125, 29]
    assert t["fontFamily"] == "Inter" and t["fontWeight"] == 700 and t["color"] == "#1A1A2E"
    assert [b["name"] for b in specs["boxes"]] == ["Button"]
    assert specs["boxes"][0]["fill"] == "#4F46E5"
    assert specs["boxes"][0]["layout"]["padding"] == [14, 24, 14, 24]


def test_annotate_attaches_figma_specs():
    specs = figma.extract_specs(FRAME)
    s = 3.0
    title = Element(id="t1", kind="text", text="Đăng nhập", rect=Rect(16.3 * s, 44.7 * s, 124.7 * s, 24 * s))
    button = Element(id="b1", kind="box", rect=Rect(16 * s, 329 * s, 343 * s, 48 * s))
    a = Analysis(image=np.zeros((10, 10, 3), np.uint8), scale=s, source_scale=s, background=(255, 255, 255), elements=[title, button])
    figma.annotate(a, specs)
    assert title.figma["fontSize"] == 24 and title.figma["fontFamily"] == "Inter"
    assert button.figma["name"] == "Button"
    assert button.label() == "Khối “Button”"


def test_fetch_uses_node_and_image_endpoints(monkeypatch):
    calls = []

    def fake_get(client, url, **params):
        calls.append((url, params))
        if "/nodes" in url:
            return {"nodes": {"1:2": {"document": FRAME}}}
        if "/images/" in url:
            return {"images": {"1:2": "https://s3.example/render.png"}}
        raise AssertionError(url)

    class FakeResponse:
        content = b"PNG"

        def raise_for_status(self):
            pass

    monkeypatch.setattr(figma, "_get", fake_get)
    monkeypatch.setattr(figma.httpx, "get", lambda *a, **k: FakeResponse())
    png, scale, specs = figma.fetch("https://www.figma.com/design/KEY/App?node-id=1-2", token="t")
    assert png == b"PNG" and scale == 3
    assert specs["node_id"] == "1:2" and specs["file_key"] == "KEY"
    assert calls[1][1]["scale"] == 3 and calls[1][1]["use_absolute_bounds"] == "true"


def test_fetch_requires_token(monkeypatch):
    monkeypatch.delenv("FIGMA_TOKEN", raising=False)
    with pytest.raises(figma.FigmaError):
        figma.fetch("https://www.figma.com/design/KEY/App?node-id=1-2")
