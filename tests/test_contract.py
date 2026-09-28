"""变量契约审计模块与 /api/contract-audit 接口测试。"""
import pytest

from app import app
from contract import audit_contract


@pytest.fixture()
def client():
    app.testing = True
    return app.test_client()


TPL = (
    "{gender, select, male{He} female{She} other{They}} "
    "{count, plural, =0{none} one{# msg} other{# msgs}}"
)


# ---------------- 契约推导 ----------------

def test_derive_types_and_requiredness():
    r = audit_contract(TPL, [])
    by_name = {c["name"]: c for c in r["contracts"]}
    # 两个变量都在顶层分支节点使用 → 必需
    assert by_name["gender"]["resolved_type"] == "string"
    assert by_name["gender"]["required"] is True
    assert by_name["gender"]["select"]["selectors"] == [
        "male", "female", "other"]
    assert by_name["count"]["resolved_type"] == "number"
    assert by_name["count"]["required"] is True
    # count 同时作为 plural 选择变量和 # 出现
    assert "plural" in by_name["count"]["usage_kinds"]
    assert "hash" in by_name["count"]["usage_kinds"]
    assert r["summary"]["required_total"] == 2
    assert r["summary"]["conditional_total"] == 0


def test_conditional_variable_inside_branch():
    tpl = "{g, select, a{{inner, number}} other{z}}"
    r = audit_contract(tpl, [])
    by_name = {c["name"]: c for c in r["contracts"]}
    assert by_name["inner"]["required"] is False
    assert by_name["inner"]["required_kind"] == "conditional"
    assert by_name["inner"]["resolved_type"] == "number"
    assert any(p.startswith("g:select@a") for p in by_name["inner"]["guards"])


def test_simple_placeholder_is_any_scalar():
    r = audit_contract("{name}-{d, date}", [])
    by_name = {c["name"]: c for c in r["contracts"]}
    assert by_name["name"]["resolved_type"] == "any"
    assert by_name["name"]["allowed_types"] == ["string", "number", "boolean"]
    assert by_name["d"]["resolved_type"] == "any"


def test_template_type_conflict_number_vs_string():
    # 同一变量先用于 number，再用于 select
    r = audit_contract("{n, number} {n, select, a{A} other{B}}", [])
    by_name = {c["name"]: c for c in r["contracts"]}
    assert by_name["n"]["type_conflict"] is True
    assert by_name["n"]["resolved_type"] == "conflict"
    assert set(by_name["n"]["allowed_types"]) == {"number", "string"}
    assert r["summary"]["type_conflicts_in_template"] == 1
    assert r["template_issues"][0]["code"] == "template_type_conflict"


def test_first_position_line_col():
    r = audit_contract("xx {a}\nyy {b, number}", [])
    by_name = {c["name"]: c for c in r["contracts"]}
    assert (by_name["a"]["first_line"], by_name["a"]["first_col"]) == (1, 4)
    assert (by_name["b"]["first_line"], by_name["b"]["first_col"]) == (2, 4)


def test_json_schema_draft():
    r = audit_contract("{g, select, a{A} other{B}} {n, plural, one{#} other{#s}}",
                       [])
    schema = r["json_schema"]
    assert schema["$schema"].endswith("draft-07/schema#")
    assert schema["type"] == "object"
    assert set(schema["required"]) == {"g", "n"}
    assert schema["properties"]["g"]["type"] == "string"
    assert schema["properties"]["n"]["type"] == "number"
    assert schema["additionalProperties"] is True
    assert "分支键" in schema["properties"]["g"]["description"]


def test_json_schema_enum_without_other():
    # 解析器对 select 缺 other 报错，但仍给出可恢复 AST；枚举来自实际分支
    r = audit_contract("{s, select, a{A} b{B}}", [])
    prop = r["json_schema"]["properties"]["s"]
    assert prop["enum"] == ["a", "b"]
    assert r["template_ok"] is False


# ---------------- 场景批量审计 ----------------

def test_batch_report_all_issue_types():
    scenarios = [
        {"id": "s1", "name": "正常", "locale": "en",
         "variables": {"gender": "male", "count": 1}},
        {"id": "s2", "name": "缺失", "locale": "en", "variables": {}},
        {"id": "s3", "name": "复数值非法", "locale": "en",
         "variables": {"gender": "male", "count": "many"}},
        {"id": "s4", "name": "多余变量", "locale": "en",
         "variables": {"gender": "x", "count": 2, "ghost": 1}},
    ]
    r = audit_contract(TPL, scenarios)
    assert r["summary"]["scenarios_total"] == 4
    assert r["summary"]["scenarios_passed"] == 2
    assert r["summary"]["scenarios_failed"] == 2
    codes = [(i["code"], i["variable"]) for i in r["issues"]]
    assert ("missing_required", "gender") in codes
    assert ("missing_required", "count") in codes
    assert ("invalid_plural_value", "count") in codes
    assert ("unused_variable", "ghost") in codes


def test_number_format_type_conflict():
    r = audit_contract("{p, number, percent}", [
        {"name": "数字字符串可接受", "locale": "en", "variables": {"p": "1.5"}},
        {"name": "非数字", "locale": "en", "variables": {"p": "abc"}},
        {"name": "布尔", "locale": "en", "variables": {"p": True}},
    ])
    by_scenario = {s["name"]: s for s in r["scenarios"]}
    assert by_scenario["数字字符串可接受"]["status"] == "passed"
    assert by_scenario["非数字"]["status"] == "failed"
    assert by_scenario["布尔"]["status"] == "failed"
    codes = {(i["scenario"]["name"], i["code"]) for i in r["issues"]}
    assert ("非数字", "type_conflict") in codes
    assert ("布尔", "type_conflict") in codes


def test_invalid_selector_without_other():
    r = audit_contract("{s, select, a{A} b{B}}", [
        {"name": "命中", "locale": "en", "variables": {"s": "a"}},
        {"name": "未命中", "locale": "en", "variables": {"s": "c"}},
    ])
    assert r["scenarios"][0]["status"] == "passed"
    assert r["scenarios"][1]["status"] == "failed"
    issue = next(i for i in r["issues"] if i["code"] == "invalid_selector")
    assert issue["variable"] == "s"
    # 首次出现位置指向该 select 用法
    assert issue["template_line"] >= 1


def test_other_fallback_allows_any_key():
    r = audit_contract("{s, select, a{A} other{B}}", [
        {"name": "任意键", "locale": "en", "variables": {"s": "whatever"}},
    ])
    assert r["scenarios"][0]["status"] == "passed"
    assert not [i for i in r["issues"] if i["code"] == "invalid_selector"]


def test_aggregation_by_variable_records_first_occurrence():
    scenarios = [
        {"name": f"c{i}", "locale": "en", "variables": {"count": "x"}}
        for i in range(3)
    ]
    r = audit_contract(TPL, scenarios)
    count_entry = next(v for v in r["variables"]
                       if v["contract"]["name"] == "count")
    group = next(g for g in count_entry["groups"]
                 if g["code"] == "invalid_plural_value")
    assert group["count"] == 3
    assert group["first"]["name"] == "c0"
    assert len(group["occurrences"]) == 3
    assert group["occurrences"][1]["scenario"]["index"] == 1


def test_aggregate_extra_variable_entry():
    r = audit_contract("{a}", [
        {"name": "s", "locale": "en", "variables": {"a": 1, "z": 9}},
    ])
    extra = next(v for v in r["variables"] if not v["in_template"])
    assert extra["contract"]["name"] == "z"
    assert extra["groups"][0]["code"] == "unused_variable"


def test_invalid_scenario_locale_and_variables():
    r = audit_contract(TPL, [
        {"name": "locale 坏", "locale": 9, "variables": {}},
        {"name": "variables 坏", "locale": "en", "variables": [1]},
    ])
    assert all(s["status"] == "failed" for s in r["scenarios"])
    assert all(i["code"] == "invalid_scenario" for i in r["issues"])


def test_numeric_string_accepted_for_plural():
    r = audit_contract("{n, plural, one{#} other{#s}}", [
        {"name": "数字字符串", "locale": "en", "variables": {"n": "2"}},
    ])
    assert r["scenarios"][0]["status"] == "passed"


# ---------------- HTTP 接口 ----------------

def test_endpoint_audit(client):
    resp = client.post("/api/contract-audit", json={
        "template": TPL,
        "scenarios": [
            {"name": "正常", "locale": "en",
             "variables": {"gender": "male", "count": 1}},
            {"name": "缺失", "locale": "en", "variables": {}},
        ],
    })
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["ok"] is True
    assert data["summary"]["missing_total"] == 2
    assert data["json_schema"]["type"] == "object"
    assert {c["name"] for c in data["contracts"]} == {"gender", "count"}


def test_endpoint_empty_scenarios_defaults(client):
    resp = client.post("/api/contract-audit",
                       json={"template": TPL})
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["summary"]["scenarios_total"] == 0
    assert data["summary"]["variables_total"] == 2


def test_endpoint_rejects_bad_body(client):
    resp = client.post("/api/contract-audit",
                       data="[]", content_type="application/json")
    assert resp.status_code == 400
    assert resp.get_json()["ok"] is False


def test_endpoint_rejects_non_string_template(client):
    resp = client.post("/api/contract-audit",
                       json={"template": 1, "scenarios": []})
    assert resp.status_code == 400


def test_endpoint_rejects_non_list_scenarios(client):
    resp = client.post("/api/contract-audit",
                       json={"template": "x", "scenarios": {}})
    assert resp.status_code == 400


def test_endpoint_too_many_scenarios(client, monkeypatch):
    import app as app_module
    monkeypatch.setattr(app_module, "MAX_SCENARIOS", 2)
    resp = client.post("/api/contract-audit", json={
        "template": "x",
        "scenarios": [{"name": str(i), "locale": "en", "variables": {}}
                      for i in range(3)],
    })
    assert resp.status_code == 400
    assert "最多" in resp.get_json()["error"]


def test_endpoint_non_object_scenario(client):
    resp = client.post("/api/contract-audit", json={
        "template": "x", "scenarios": [42],
    })
    assert resp.status_code == 400
