"""API integration tests for health, metrics, devices, and guard endpoints."""

import pytest
from fastapi.testclient import TestClient

from app.main import create_app


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("WARDEN_DATA_DIR", str(tmp_path / "data"))
    app = create_app()
    with TestClient(app) as c:
        yield c


def test_healthz(client):
    res = client.get("/healthz")
    assert res.status_code == 200
    assert res.json()["status"] == "ok"


def test_metrics(client):
    res = client.get("/metrics")
    assert res.status_code == 200
    assert "warden_devices_total" in res.text


def test_channel_rules_crud(client):
    # List (default rule seeded)
    res = client.get("/api/guard/rules")
    assert res.status_code == 200
    rules = res.json()
    assert len(rules) >= 1
    assert "Fox News" in rules[0]["name"]

    # Create new rule
    new_rule = {
        "name": "Block Test Channel",
        "enabled": True,
        "target_packages": ["com.google.android.youtube.tvunplugged"],
        "patterns": ["test\\s*channel"],
        "action": "auto_skip",
        "description": "Test rule",
    }
    create_res = client.post("/api/guard/rules", json=new_rule)
    assert create_res.status_code == 201
    created_id = create_res.json()["id"]

    # Patch
    patch_res = client.patch(f"/api/guard/rules/{created_id}", json={"action": "force_stop"})
    assert patch_res.status_code == 200
    assert patch_res.json()["action"] == "force_stop"

    # Delete
    del_res = client.delete(f"/api/guard/rules/{created_id}")
    assert del_res.status_code == 204


def test_pattern_tester(client):
    res = client.post(
        "/api/guard/test-pattern",
        json={"pattern": r"fox\s*news", "sample_text": "Live: Fox News Channel HD"}
    )
    assert res.status_code == 200
    data = res.json()
    assert data["matched"] is True
    assert data["matched_text"] == "Fox News"
