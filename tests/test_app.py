"""/api/scenarios/run 接口测试。"""
import json

import pytest

from app import app


@pytest.fixture()
def client():
    app.testing = True
    return app.test_client()


TPL = "{g, select, male{He} other{They}} {n, plural, one{#} other{#s}}"


def test_scenarios_run_merge(client):
    resp = client.post("/api/scenarios/run", json={
        "template": TPL,
        "scenarios": [
            {"id": "s1", "name": "男-1", "locale": "en",
             "variables": {"g": "male", "n": 1}},
            {"id": "s2", "name": "其他-2", "locale": "en",
             "variables": {"g": "x", "n": 2}},
        ],
    })
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["summary"]["total_scenarios"] == 2
    assert data["summary"]["passed_scenarios"] == 2
    # male/one、male/other、other/one、other/other = 4 条，覆盖 2 条
    assert data["summary"]["total_paths"] == 4
    assert data["summary"]["covered_paths"] == 2
    # 完整 analyze 结果随场景返回
    s1 = data["scenarios"][0]
    assert s1["result"]["main"]["output"] == "He 1"
    assert s1["result"]["paths"][0]["key"]


def test_scenarios_run_failure_does_not_abort_batch(client):
    resp = client.post("/api/scenarios/run", json={
        "template": TPL,
        "scenarios": [
            {"id": "s1", "name": "好", "locale": "en",
             "variables": {"g": "male", "n": 1}},
            {"id": "s2", "name": "坏locale", "locale": 9, "variables": {}},
            {"id": "s3", "name": "坏变量", "locale": "en",
             "variables": {"g": "male", "n": "x"}},
        ],
    })
    assert resp.status_code == 200
    data = resp.get_json()
    statuses = {s["id"]: s["status"] for s in data["scenarios"]}
    assert statuses == {"s1": "passed", "s2": "failed", "s3": "failed"}
    assert data["summary"]["failed_scenarios"] == 2
    assert data["summary"]["covered_paths"] == 1


def test_scenarios_run_rejects_non_dict_body(client):
    resp = client.post("/api/scenarios/run",
                       data="[]", content_type="application/json")
    assert resp.status_code == 400
    assert resp.get_json()["ok"] is False


def test_scenarios_run_rejects_non_list(client):
    resp = client.post("/api/scenarios/run",
                       json={"template": "x", "scenarios": {}})
    assert resp.status_code == 400


def test_scenarios_run_too_many(client, monkeypatch):
    import app as app_module
    monkeypatch.setattr(app_module, "MAX_SCENARIOS", 2)
    resp = client.post("/api/scenarios/run", json={
        "template": "x",
        "scenarios": [{"name": str(i), "locale": "en", "variables": {}}
                      for i in range(3)],
    })
    assert resp.status_code == 400
    assert "最多" in resp.get_json()["error"]


def test_preview_paths_include_key(client):
    resp = client.post("/api/preview", json={
        "template": TPL, "locale": "en",
        "variables": {"g": "male", "n": 1},
    })
    data = resp.get_json()
    assert all("key" in p for p in data["paths"])
    assert data["paths"][0]["key"].startswith("n1:")


def test_health(client):
    resp = client.get("/api/health")
    assert resp.status_code == 200
    assert json.loads(resp.data)["status"] == "ok"
