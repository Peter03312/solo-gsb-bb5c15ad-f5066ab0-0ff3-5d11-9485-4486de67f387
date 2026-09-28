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


def test_branch_guarded_variable_used_in_every_branch_is_required():
    """变量只写在分支内，但每条完整路径都会用到 → 必需（顺序组合 + 分支交集）。"""
    tpl = "{g, select, male{He {who}} female{She {who}} other{They {who}}}"
    r = audit_contract(tpl, [])
    by_name = {c["name"]: c for c in r["contracts"]}
    assert by_name["g"]["required"] is True          # 选择变量恒必需
    assert by_name["who"]["required"] is True        # 每个分支都用
    assert by_name["who"]["required_kind"] == "required"
    assert any(p.startswith("g:select@male") for p in by_name["who"]["guards"])
    assert r["summary"]["required_total"] == 2
    assert r["summary"]["conditional_total"] == 0
    assert set(r["json_schema"]["required"]) == {"g", "who"}


def test_same_variable_split_across_branches_is_required():
    """同一变量分散在不同分支，且分支集恰好覆盖所有路径 → 必需。"""
    tpl = ("{g, select, a{{x, number}} other{{x, number}}}")
    r = audit_contract(tpl, [])
    by_name = {c["name"]: c for c in r["contracts"]}
    assert by_name["x"]["required"] is True
    assert "x" in r["json_schema"]["required"]


def test_variable_used_in_some_branches_stays_conditional():
    """仅部分路径使用的变量仍为条件必需。"""
    tpl = "{g, select, a{{x}} other{no-x}}"
    r = audit_contract(tpl, [])
    by_name = {c["name"]: c for c in r["contracts"]}
    assert by_name["g"]["required"] is True
    assert by_name["x"]["required"] is False
    assert by_name["x"]["required_kind"] == "conditional"
    assert "x" not in r["json_schema"]["required"]


def test_nested_branch_requiredness_intersection():
    """嵌套 select：内层变量在外层的每个分支、内层的每个分支都使用才必需。"""
    tpl_cover_all = (
        "{g, select, "
        "a{{h, select, m{{x}} other{{x}}}} "
        "other{{h, select, m{{x}} other{{x}}}}}"
    )
    r = audit_contract(tpl_cover_all, [])
    by_name = {c["name"]: c for c in r["contracts"]}
    assert by_name["g"]["required"] is True
    assert by_name["h"]["required"] is True
    assert by_name["x"]["required"] is True

    tpl_partial = (
        "{g, select, "
        "a{{h, select, m{{x}} other{}}} "
        "other{{h, select, m{} other{{x}}}}}"
    )
    r2 = audit_contract(tpl_partial, [])
    by2 = {c["name"]: c for c in r2["contracts"]}
    # 内层 select 选择变量 h 在外层每条路径都进入，故必需；x 只覆盖部分路径
    assert by2["h"]["required"] is True
    assert by2["x"]["required"] is False
    assert by2["x"]["required_kind"] == "conditional"


def test_plural_and_selectordinal_inner_variable_requiredness():
    tpl = ("{p, selectordinal, one{#st {x}} two{#nd {x}} "
           "few{#rd {x}} other{#th {x}}}")
    r = audit_contract(tpl, [])
    by_name = {c["name"]: c for c in r["contracts"]}
    assert by_name["p"]["required"] is True   # 选择变量
    assert by_name["x"]["required"] is True   # 每个分支都用
    assert "x" in r["json_schema"]["required"]

    tpl2 = "{n, plural, =0{none} one{# {x}} other{# {x}}}"
    r2 = audit_contract(tpl2, [])
    by2 = {c["name"]: c for c in r2["contracts"]}
    assert by2["n"]["required"] is True
    assert by2["x"]["required"] is False      # =0 分支不用 x
    assert "x" not in r2["json_schema"]["required"]


def test_hash_owner_requiredness():
    """plural 内 # 属主恒为选择变量；分支内其他变量按路径交集判定。"""
    tpl = "{n, plural, one{# {y}} other{#}}"
    r = audit_contract(tpl, [])
    by_name = {c["name"]: c for c in r["contracts"]}
    assert by_name["n"]["required"] is True
    assert by_name["y"]["required"] is False


def test_branch_guarded_required_missing_is_error():
    """每条路径都使用的分支内变量缺失 → error，且场景失败；条件变量仍为 warning。"""
    tpl = "{g, select, male{{x}} female{{x}} other{{x}}}"
    r = audit_contract(tpl, [
        {"name": "缺 x", "locale": "en", "variables": {"g": "male"}},
        {"name": "完整", "locale": "en", "variables": {"g": "male", "x": 1}},
    ])
    codes = [(i["code"], i["severity"], i["variable"]) for i in r["issues"]]
    assert ("missing_required", "error", "x") in codes
    assert not any(c[0] == "missing_conditional" for c in codes)
    assert r["scenarios"][0]["status"] == "failed"
    assert r["scenarios"][1]["status"] == "passed"
    assert set(r["json_schema"]["required"]) == {"g", "x"}

    tpl2 = "{g, select, a{{x}} other{z}}"
    r2 = audit_contract(tpl2, [
        {"name": "走 a 但缺 x", "locale": "en", "variables": {"g": "a"}},
    ])
    codes2 = {(i["code"], i["severity"]) for i in r2["issues"]}
    assert ("missing_conditional", "warning") in codes2
    assert "x" not in r2["json_schema"]["required"]
    assert r2["scenarios"][0]["status"] == "passed"


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
