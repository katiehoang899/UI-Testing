import shutil

import pytest
from fastapi.testclient import TestClient

from synth import font_available, render
from uicheck.server import app

client = TestClient(app)


def test_index_and_static():
    assert "UI Spec Checker" in client.get("/").text
    assert client.get("/static/app.js").status_code == 200


def test_requires_design_or_figma():
    r = client.post("/api/compare", files={"dev": ("dev.png", b"x", "image/png")})
    assert r.status_code == 400


def test_bad_image():
    r = client.post(
        "/api/compare",
        files={"dev": ("dev.png", b"not an image", "image/png"), "design": ("d.png", b"nope", "image/png")},
    )
    assert r.status_code == 400


@pytest.mark.skipif(shutil.which("tesseract") is None or not font_available(), reason="needs tesseract and fonts")
def test_compare_endpoint():
    r = client.post(
        "/api/compare",
        files={"dev": ("dev.png", render(card_padding=24), "image/png"), "design": ("d.png", render(), "image/png")},
        data={"tol_spacing": "2", "checks": "padding"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["design"]["image"].startswith("data:image/")
    assert body["summary"]["by_group"]["spacing"] == 4
    assert {i["category"] for i in body["issues"]} == {"padding"}
