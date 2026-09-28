"""ICU 文案分支预览器 — Flask 入口。"""
import os

from flask import Flask, jsonify, render_template, request

from compare import compare_templates
from contract import audit_contract
from icu import MAX_SCENARIOS, analyze, run_scenarios

app = Flask(__name__)
app.json.ensure_ascii = False  # 响应中保留中文


@app.get("/")
def index():
    return render_template("index.html")


@app.post("/api/preview")
def preview():
    data = request.get_json(force=True, silent=True) or {}
    template = data.get("template", "")
    locale = data.get("locale", "en")
    variables = data.get("variables", {})
    if not isinstance(template, str):
        return jsonify({"ok": False, "errors": [{
            "message": "template 必须是字符串", "pos": 0, "length": 1,
            "line": 1, "col": 1, "severity": "error"}]}), 400
    if not isinstance(variables, dict):
        variables = {}
    return jsonify(analyze(template, locale, variables))


@app.post("/api/scenarios/run")
def scenarios_run():
    """场景集回归：批量分析同一模板下的多组 locale/变量并合并路径覆盖。"""
    data = request.get_json(force=True, silent=True)
    if not isinstance(data, dict):
        return jsonify({"ok": False, "error": "请求体必须是 JSON 对象"}), 400
    template = data.get("template", "")
    scenarios = data.get("scenarios", [])
    if not isinstance(template, str):
        return jsonify({"ok": False, "error": "template 必须是字符串"}), 400
    if not isinstance(scenarios, list):
        return jsonify({"ok": False, "error": "scenarios 必须是数组"}), 400
    if len(scenarios) > MAX_SCENARIOS:
        return jsonify({"ok": False,
                        "error": f"单次最多运行 {MAX_SCENARIOS} 个场景"}), 400
    norm = []
    for sc in scenarios:
        if not isinstance(sc, dict):
            return jsonify({"ok": False, "error": "每个场景必须是对象"}), 400
        norm.append({
            "id": sc.get("id"),
            "name": str(sc.get("name") or ""),
            "locale": sc.get("locale", "en"),
            "variables": sc.get("variables", {}),
        })
    return jsonify(run_scenarios(template, norm))


@app.post("/api/compare")
def compare():
    """版本对比：语义对齐新旧模板，返回结构差异与场景双版回放结果。"""
    data = request.get_json(force=True, silent=True)
    if not isinstance(data, dict):
        return jsonify({"ok": False, "error": "请求体必须是 JSON 对象"}), 400
    old_template = data.get("old_template", "")
    new_template = data.get("new_template", "")
    if not isinstance(old_template, str) or not isinstance(new_template, str):
        return jsonify({"ok": False,
                        "error": "old_template 与 new_template 必须是字符串"}), 400
    scenarios = data.get("scenarios")
    if scenarios is None:
        scenarios = []
    if not isinstance(scenarios, list):
        return jsonify({"ok": False, "error": "scenarios 必须是数组"}), 400
    if len(scenarios) > MAX_SCENARIOS:
        return jsonify({"ok": False,
                        "error": f"单次最多回放 {MAX_SCENARIOS} 个场景"}), 400
    norm = []
    for sc in scenarios:
        if not isinstance(sc, dict):
            return jsonify({"ok": False, "error": "每个场景必须是对象"}), 400
        norm.append({
            "id": sc.get("id"),
            "name": str(sc.get("name") or ""),
            "locale": sc.get("locale", "en"),
            "variables": sc.get("variables", {}),
        })
    return jsonify(compare_templates(old_template, new_template, norm))


@app.post("/api/contract-audit")
def contract_audit():
    """变量契约审计：推导变量类型契约，批量审计多组场景样例。"""
    data = request.get_json(force=True, silent=True)
    if not isinstance(data, dict):
        return jsonify({"ok": False, "error": "请求体必须是 JSON 对象"}), 400
    template = data.get("template", "")
    scenarios = data.get("scenarios", [])
    if not isinstance(template, str):
        return jsonify({"ok": False, "error": "template 必须是字符串"}), 400
    if not isinstance(scenarios, list):
        return jsonify({"ok": False, "error": "scenarios 必须是数组"}), 400
    if len(scenarios) > MAX_SCENARIOS:
        return jsonify({"ok": False,
                        "error": f"单次最多审计 {MAX_SCENARIOS} 个场景"}), 400
    norm = []
    for idx, sc in enumerate(scenarios):
        if not isinstance(sc, dict):
            return jsonify({"ok": False,
                            "error": f"第 {idx + 1} 个场景必须是对象"}), 400
        norm.append({
            "id": sc.get("id", idx + 1),
            "name": str(sc.get("name") or "") or f"场景 {idx + 1}",
            "locale": sc.get("locale", "en"),
            "variables": sc.get("variables")
            if isinstance(sc.get("variables"), dict) else {},
        })
    return jsonify(audit_contract(template, norm))


@app.get("/api/health")
def health():
    return jsonify({"status": "ok"})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=False)
