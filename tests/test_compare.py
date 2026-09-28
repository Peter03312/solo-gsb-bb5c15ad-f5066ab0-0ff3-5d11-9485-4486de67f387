"""版本对比（compare.py 与 /api/compare）的测试。"""
import pytest

from app import app
from compare import collect_nodes, compare_templates, diff_parts
from icu import Parser


def parse(text):
    p = Parser(text)
    return p.parse()


def types(result):
    return [c["type"] for c in result["changes"]]


# ---------------- 语义路径 ----------------

def test_semantic_path_uses_name_kind_selector_and_parent_chain():
    parts = parse("{g, select, male{{n, plural, one{# file} other{# files}}} "
                  "other{none}}")
    infos = collect_nodes(parts)
    paths = [i["path"] for i in infos]
    assert paths == ["g:select", "g:select@male/n:plural"]


def test_semantic_path_disambiguates_same_signature_siblings():
    parts = parse("{n, plural, one{a} other{b}}-{n, plural, one{c} other{d}}")
    paths = [i["path"] for i in collect_nodes(parts)]
    assert paths == ["n:plural", "n:plural[2]"]


def test_alignment_does_not_depend_on_node_order():
    # 新版在最前面插入一个节点：n1/n2 序号全变，但语义对齐不受影响
    old = "{a, select, x{X} other{O}}"
    new = "{b, select, y{Y} other{O}} {a, select, x{X} other{O}}"
    res = compare_templates(old, new)
    added = [c for c in res["changes"] if c["type"] == "added"]
    assert len(added) == 1
    assert added[0]["variable"] == "b"
    assert added[0]["level"] == "node"
    # a 节点及其分支不产生任何增删 / 条件 / 文本变更
    assert not [c for c in res["changes"]
                if c["type"] in ("removed", "condition")
                or (c["type"] == "render" and c["level"] != "message")]


# ---------------- 新增 / 删除 ----------------

def test_added_branch():
    old = "{g, select, male{He} other{They}}"
    new = "{g, select, male{He} female{She} other{They}}"
    res = compare_templates(old, new)
    assert types(res) == ["added"]
    ch = res["changes"][0]
    assert ch["path"] == "g:select@female"
    assert ch["selector"] == "female"
    assert ch["new"]["text"] == "female{She}"
    assert ch["old"] is None


def test_removed_branch():
    old = "{g, select, male{He} female{She} other{They}}"
    new = "{g, select, male{He} other{They}}"
    res = compare_templates(old, new)
    assert types(res) == ["removed"]
    ch = res["changes"][0]
    assert ch["path"] == "g:select@female"
    assert ch["old"]["text"] == "female{She}"
    assert ch["new"] is None


def test_added_whole_node_reports_once():
    # 嵌套节点整体新增：只报告最外层，不逐个报告后代
    old = "Hello"
    new = "{g, select, male{{n, plural, one{# f} other{# fs}}} other{x}}"
    res = compare_templates(old, new)
    added = [c for c in res["changes"] if c["type"] == "added"]
    assert len(added) == 1
    assert added[0]["path"] == "g:select"


def test_removed_nested_node_reports_once():
    old = "{g, select, male{{n, plural, one{# f} other{# fs}}} other{x}}"
    new = "Hello"
    res = compare_templates(old, new)
    removed = [c for c in res["changes"] if c["type"] == "removed"]
    assert len(removed) == 1
    assert removed[0]["path"] == "g:select"


def test_top_level_text_change():
    res = compare_templates("Hello {name}", "Hi {name}")
    assert types(res) == ["render"]
    ch = res["changes"][0]
    assert ch["level"] == "message"
    assert "顶层" in ch["detail"]


# ---------------- 条件变化 ----------------

def test_condition_offset_change():
    old = "{n, plural, offset:1 one{you} other{you and # others}}"
    new = "{n, plural, offset:2 one{you} other{you and # others}}"
    res = compare_templates(old, new)
    assert types(res) == ["condition"]
    ch = res["changes"][0]
    assert ch["level"] == "node"
    assert "offset" in ch["detail"]
    assert "1" in ch["detail"] and "2" in ch["detail"]


def test_condition_kind_change_plural_to_ordinal():
    old = "{n, plural, one{#st} other{#th}}"
    new = "{n, selectordinal, one{#st} other{#th}}"
    res = compare_templates(old, new)
    # 类型变化 → 条件变化；分支仍按选择器对齐，不产生增删
    assert types(res) == ["condition"]
    ch = res["changes"][0]
    assert "plural" in ch["detail"] and "selectordinal" in ch["detail"]
    assert ch["old_path"] == "n:plural"
    assert ch["path"] == "n:selectordinal"


def test_condition_explicit_selector_change():
    old = "{n, plural, =0{none} one{one} other{many}}"
    new = "{n, plural, =1{none} one{one} other{many}}"
    res = compare_templates(old, new)
    assert types(res) == ["condition"]
    ch = res["changes"][0]
    assert "=0" in ch["detail"] and "=1" in ch["detail"]


# ---------------- 渲染文本变化 ----------------

def test_render_text_change():
    old = "{g, select, male{He has} other{They have}} {n, plural, one{# msg} other{# msgs}}"
    new = "{g, select, male{He got} other{They have}} {n, plural, one{# msg} other{# msgs}}"
    res = compare_templates(old, new)
    assert types(res) == ["render"]
    ch = res["changes"][0]
    assert ch["path"] == "g:select@male"
    assert ch["old"]["text"] == "male{He has}"
    assert ch["new"]["text"] == "male{He got}"


def test_nested_change_not_double_reported_on_parent():
    # 嵌套节点内部文本变化只在其自身路径上报告，父分支不重复
    old = "{g, select, male{{n, plural, one{his file} other{his files}}} other{x}}"
    new = "{g, select, male{{n, plural, one{her file} other{his files}}} other{x}}"
    res = compare_templates(old, new)
    assert types(res) == ["render"]
    assert res["changes"][0]["path"] == "g:select@male/n:plural@one"


def test_identical_templates_have_no_changes():
    tpl = "{g, select, male{He} other{They}} {n, plural, one{#} other{#s}}"
    res = compare_templates(tpl, tpl)
    assert res["changes"] == []
    assert res["summary"]["total"] == 0


def test_summary_counts():
    old = "{g, select, male{He} female{She} other{They}}"
    new = "{g, select, male{He is} other{They}} {n, plural, one{#} other{#s}}"
    res = compare_templates(old, new)
    sm = res["summary"]
    assert sm["removed"] == 1   # female
    assert sm["render"] == 2    # male 分支文本 + 顶层新增的空格
    assert sm["added"] == 1     # 新 plural 节点
    assert sm["total"] == 4


# ---------------- 解析错误与场景回放 ----------------

def test_side_parse_errors_are_kept_separately():
    old = "{n, plural, one{a}}"          # 缺 other → 基线侧有错误
    new = "{n, plural, one{a} other{b}}"
    res = compare_templates(old, new)
    assert res["ok"] is True             # 请求本身成功
    assert res["old"]["ok"] is False
    assert res["old"]["errors"]
    assert res["new"]["ok"] is True
    # 仍基于可恢复的 AST 计算差异
    assert "added" in types(res)


def test_scenarios_replayed_on_both_versions():
    old = "{g, select, male{He} other{They}}"
    new = "{g, select, male{He} other{They}}!"
    res = compare_templates(old, new, [
        {"id": "s1", "name": "男性", "locale": "en", "variables": {"g": "male"}},
        {"id": "s2", "name": "其他", "locale": "en", "variables": {"g": "x"}},
    ])
    sc = res["scenarios"]
    assert sc["total"] == 2
    assert sc["changed"] == 2
    items = {i["id"]: i for i in sc["items"]}
    assert items["s1"]["old"]["output"] if False else items["s1"]["old"]["output"] == "He"
    assert items["s1"]["new"]["output"] == "He!"
    assert items["s1"]["changed"] is True


def test_scenario_one_side_failure_isolated():
    old = "{n, plural, one{# item} other{# items}}"
    new = "{n, plural, one{# item} other{# items}"   # 未闭合 → 新侧失败
    res = compare_templates(old, new, [
        {"id": "s1", "name": "一个", "locale": "en", "variables": {"n": 1}},
        {"id": "s2", "name": "多个", "locale": "en", "variables": {"n": 3}},
        {"id": "s3", "name": "坏locale", "locale": 9, "variables": {}},
    ])
    items = {i["id"]: i for i in res["scenarios"]["items"]}
    # 新侧解析失败只影响新侧，旧侧照常运行，其余场景不被阻断
    assert items["s1"]["old"]["status"] == "passed"
    assert items["s1"]["new"]["status"] == "failed"
    assert items["s1"]["new"]["error"]
    assert items["s1"]["changed"] is False
    assert items["s2"]["old"]["status"] == "passed"
    assert items["s2"]["new"]["status"] == "failed"
    # 场景自身参数错误在两侧都保留
    assert items["s3"]["old"]["status"] == "failed"
    assert "locale" in items["s3"]["old"]["error"]
    assert res["scenarios"]["new_failed"] == 3
    assert res["scenarios"]["old_failed"] == 1


def test_scenario_variable_type_error_kept_on_that_side():
    tpl = "{n, plural, one{#} other{#s}}"
    res = compare_templates(tpl, tpl, [
        {"id": "s1", "name": "坏变量", "locale": "en", "variables": {"n": "abc"}},
        {"id": "s2", "name": "好", "locale": "en", "variables": {"n": 2}},
    ])
    items = {i["id"]: i for i in res["scenarios"]["items"]}
    assert items["s1"]["old"]["status"] == "failed"
    assert "需要数字" in items["s1"]["old"]["error"]
    assert items["s2"]["old"]["status"] == "passed"
    assert items["s2"]["changed"] is False


# ---------------- HTTP 接口 ----------------

@pytest.fixture()
def client():
    app.testing = True
    return app.test_client()


def test_api_compare_basic(client):
    resp = client.post("/api/compare", json={
        "old_template": "{g, select, male{He} other{They}}",
        "new_template": "{g, select, male{He} female{She} other{They}}",
        "scenarios": [{"id": "s1", "name": "男", "locale": "en",
                       "variables": {"g": "male"}}],
    })
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["ok"] is True
    assert data["summary"]["added"] == 1
    assert data["changes"][0]["path"] == "g:select@female"
    assert data["scenarios"]["items"][0]["old"]["status"] == "passed"


def test_api_compare_rejects_bad_body(client):
    resp = client.post("/api/compare", data="[]",
                       content_type="application/json")
    assert resp.status_code == 400


def test_api_compare_rejects_non_string_template(client):
    resp = client.post("/api/compare",
                       json={"old_template": 1, "new_template": "x"})
    assert resp.status_code == 400


def test_api_compare_rejects_non_list_scenarios(client):
    resp = client.post("/api/compare", json={
        "old_template": "a", "new_template": "b", "scenarios": {}})
    assert resp.status_code == 400


def test_api_compare_too_many_scenarios(client, monkeypatch):
    import app as app_module
    monkeypatch.setattr(app_module, "MAX_SCENARIOS", 2)
    resp = client.post("/api/compare", json={
        "old_template": "a", "new_template": "b",
        "scenarios": [{"name": str(i), "locale": "en", "variables": {}}
                      for i in range(3)],
    })
    assert resp.status_code == 400
    assert "最多" in resp.get_json()["error"]


def test_api_compare_scenarios_optional(client):
    resp = client.post("/api/compare",
                       json={"old_template": "a", "new_template": "b"})
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["scenarios"]["total"] == 0
