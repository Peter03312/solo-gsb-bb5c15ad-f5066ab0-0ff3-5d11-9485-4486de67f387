"""变量契约审计：从模板 AST 推导变量类型契约，并对多组场景样例批量校验。

契约推导：
- 简单占位 ``{name}``                 → 任意标量（string / number / boolean）
- ``{name, number}``                  → number
- ``{name, plural}`` / ``selectordinal`` → number（plural 内的 ``#`` 同样约束其属主变量）
- ``{name, select, ...}``             → string，并记录允许的分支键
- 同一变量被 number 与 string 两类用法同时约束时，标记模板内类型冲突

必需性：
- 任一用法不处于任何分支节点守卫之下 → 必需（required）
- 全部用法都在分支内 → 条件必需（conditional），记录守卫的语义路径

场景校验批量产出三类问题（并记录其在模板与场景中的首次出现位置）：
- missing_required / missing_conditional 缺失变量
- type_conflict                           类型冲突
- invalid_plural_value                    plural / selectordinal 收到无法解析为数字的值
- invalid_selector                        select 值无匹配分支且缺少 other 兜底
- unused_variable                         样例提供了模板未使用的变量（提示）
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from icu import (
    MAX_TEMPLATE_LEN, Arg, FmtArg, Hash, Parser, Plural, Select,
    _str_value, _to_number,
)

# ---------------------------------------------------------------------------
# 常量与展示文案
# ---------------------------------------------------------------------------

KIND_LABELS = {
    "simple": "简单占位",
    "number": "number",
    "date": "date",
    "time": "time",
    "spellout": "spellout",
    "ordinal": "ordinal",
    "duration": "duration",
    "plural": "plural",
    "selectordinal": "selectordinal",
    "select": "select",
    "hash": "plural 内 #",
}

NUMBER_KINDS = ("number", "plural", "selectordinal", "hash")
PLURAL_KINDS = ("plural", "selectordinal", "hash")

SEVERITY_ERROR = "error"
SEVERITY_WARNING = "warning"
SEVERITY_INFO = "info"

_UNSET = object()


def _line_col(template: str, pos: int) -> tuple[int, int]:
    pos = max(0, min(pos, len(template)))
    line = template.count("\n", 0, pos) + 1
    last_nl = template.rfind("\n", 0, pos)
    return line, pos - last_nl


def _preview(value) -> str:
    """场景值的简短展示。"""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        s = value
        if len(s) > 30:
            s = s[:30] + "…"
        return repr(s)
    if isinstance(value, (int, float)):
        return _str_value(value)
    if isinstance(value, list):
        return f"数组（{len(value)} 项）"
    if isinstance(value, dict):
        return f"对象（{len(value)} 个键）"
    return str(value)


def _value_type_name(value) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "布尔"
    if isinstance(value, (int, float)):
        return "数字"
    if isinstance(value, str):
        return "字符串"
    if isinstance(value, list):
        return "数组"
    if isinstance(value, dict):
        return "对象"
    return type(value).__name__


# ---------------------------------------------------------------------------
# 契约推导
# ---------------------------------------------------------------------------

@dataclass
class Usage:
    kind: str
    pos: int
    constraint: str  # number | string | any
    select: dict | None = None


@dataclass
class Contract:
    name: str
    usages: list[Usage] = field(default_factory=list)
    unconditional: bool = False
    guards: list[str] = field(default_factory=list)  # 去重后的守卫语义路径

    @property
    def first_pos(self) -> int:
        return min(u.pos for u in self.usages)

    @property
    def constraints(self) -> set[str]:
        return {u.constraint for u in self.usages}

    @property
    def has_plural(self) -> bool:
        return any(u.kind in PLURAL_KINDS for u in self.usages)

    @property
    def has_select(self) -> bool:
        return any(u.kind == "select" for u in self.usages)

    @property
    def select_info(self) -> dict | None:
        infos = [u.select for u in self.usages if u.select]
        if not infos:
            return None
        selectors: list[str] = []
        for info in infos:
            for sel in info["selectors"]:
                if sel not in selectors:
                    selectors.append(sel)
        return {
            "selectors": selectors,
            # 任一 select 节点缺少 other 兜底 → 键校验需要更严格
            "has_other": all(info["has_other"] for info in infos),
        }


def _node_kind(node) -> str:
    return node.kind if isinstance(node, Plural) else "select"


def derive_contracts(template: str, parts: list) -> dict[str, Contract]:
    """遍历 AST，按变量收集用法、约束与分支守卫信息。"""
    contracts: dict[str, Contract] = {}

    def get(name: str) -> Contract:
        c = contracts.get(name)
        if c is None:
            c = Contract(name=name)
            contracts[name] = c
        return c

    def add(name: str, kind: str, pos: int, constraint: str,
            guard_chain: tuple, select: dict | None = None):
        if not name or name == "?":
            return
        c = get(name)
        c.usages.append(Usage(kind, pos, constraint, select))
        if not guard_chain:
            c.unconditional = True
        else:
            guard = "/".join(guard_chain)
            if guard not in c.guards:
                c.guards.append(guard)

    def walk(frag: list, guard_chain: tuple, plural_stack: list):
        for node in frag:
            if isinstance(node, Arg):
                add(node.name, "simple", node.pos, "any", guard_chain)
            elif isinstance(node, FmtArg):
                constraint = "number" if node.fmt == "number" else "any"
                add(node.name, node.fmt, node.pos, constraint, guard_chain)
            elif isinstance(node, Hash):
                if plural_stack:
                    owner = plural_stack[-1]
                    add(owner.name, "hash", node.pos, "number", guard_chain)
            elif isinstance(node, Plural):
                add(node.name, node.kind, node.pos, "number", guard_chain)
                label = f"{node.name}:{node.kind}"
                for b in node.branches:
                    walk(b.message,
                         guard_chain + (f"{label}@{b.selector}",),
                         plural_stack + [node])
            elif isinstance(node, Select):
                add(node.name, "select", node.pos, "string", guard_chain,
                    select={
                        "selectors": [b.selector for b in node.branches],
                        "has_other": any(b.selector == "other"
                                        for b in node.branches),
                    })
                label = f"{node.name}:select"
                for b in node.branches:
                    walk(b.message,
                         guard_chain + (f"{label}@{b.selector}",),
                         plural_stack)

    walk(parts, (), [])
    return contracts


def _resolved_type(c: Contract) -> tuple[str, bool]:
    """返回 (合并类型, 是否存在模板内冲突)。"""
    cons = c.constraints
    if "number" in cons and "string" in cons:
        return "conflict", True
    if "number" in cons:
        return "number", False
    if "string" in cons:
        return "string", False
    return "any", False


def allowed_types(resolved: str, conflict: bool) -> list[str]:
    if conflict:
        return ["number", "string"]
    if resolved == "number":
        return ["number"]
    if resolved == "string":
        return ["string"]
    return ["string", "number", "boolean"]


def _contract_dict(name: str, c: Contract, template: str) -> dict:
    resolved, conflict = _resolved_type(c)
    line, col = _line_col(template, c.first_pos)
    return {
        "name": name,
        "required": c.unconditional,
        "required_kind": "required" if c.unconditional else "conditional",
        "guards": c.guards,
        "resolved_type": resolved,
        "allowed_types": allowed_types(resolved, conflict),
        "type_conflict": conflict,
        "usage_kinds": [u.kind for u in c.usages],
        "usage_labels": [KIND_LABELS.get(u.kind, u.kind) for u in c.usages],
        "select": c.select_info,
        "first_pos": c.first_pos,
        "first_line": line,
        "first_col": col,
    }


# ---------------------------------------------------------------------------
# JSON Schema 草案
# ---------------------------------------------------------------------------

def build_json_schema(items: list[dict]) -> dict:
    """根据推导后的契约生成 draft-07 JSON Schema 草案。"""
    props: dict[str, dict] = {}
    required: list[str] = []
    for c in items:
        name = c["name"]
        sel = c.get("select")
        enum = None
        if sel and not sel["has_other"] and sel["selectors"]:
            enum = list(sel["selectors"])

        if c["type_conflict"]:
            str_alt = {"type": "string"}
            if enum:
                str_alt["enum"] = enum
            prop = {"oneOf": [{"type": "number"}, str_alt]}
        elif c["resolved_type"] == "number":
            prop = {"type": "number"}
        elif c["resolved_type"] == "string":
            prop = {"type": "string"}
            if enum:
                prop["enum"] = enum
        else:
            prop = {}

        kinds = "、".join(dict.fromkeys(c["usage_labels"]))
        desc = f"用法：{kinds}" if kinds else ""
        if sel:
            keys = "、".join(sel["selectors"])
            tail = "" if sel["has_other"] else "（无 other 兜底，取值必须命中下列分支之一）"
            desc += f"；select 分支键：{keys}{tail}"
        if c["required_kind"] == "conditional":
            guards = "；".join(c["guards"][:3])
            more = "…" if len(c["guards"]) > 3 else ""
            desc += f"；条件必需（仅在分支 {guards}{more} 中使用）"
        if c["type_conflict"]:
            desc += "；⚠ 模板内类型冲突：同时被要求为数字与字符串"
        if desc:
            prop["description"] = desc

        props[name] = prop
        if c["required"]:
            required.append(name)

    return {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "title": "ICU 模板变量契约草案",
        "type": "object",
        "properties": props,
        "required": required,
        # 条件变量与额外样例变量不禁止
        "additionalProperties": True,
    }


# ---------------------------------------------------------------------------
# 场景校验
# ---------------------------------------------------------------------------

def _is_finite_number(value) -> bool:
    num = _to_number(value)
    return num is not None and math.isfinite(num)


def _scenario_ref(sc: dict, index: int) -> dict:
    return {"index": index, "id": sc.get("id"), "name": sc.get("name") or f"场景 {index + 1}"}


def _issue(code: str, severity: str, message: str, variable: str | None,
           sc: dict | None, index: int | None, template: str,
           pos: int | None = None, value=_UNSET) -> dict:
    item = {
        "code": code,
        "severity": severity,
        "message": message,
        "variable": variable,
        "scenario": _scenario_ref(sc, index) if sc is not None else None,
        "template_pos": pos,
    }
    if pos is not None:
        line, col = _line_col(template, pos)
        item["template_line"] = line
        item["template_col"] = col
    if value is not _UNSET:
        item["value"] = value
    return item


def _expected_text(cdict: dict) -> str:
    labels = {
        "number": "数字",
        "string": "字符串",
        "any": "标量（字符串 / 数字 / 布尔）",
    }
    if cdict["type_conflict"]:
        return "数字或字符串（模板内用法冲突）"
    return labels.get(cdict["resolved_type"], "标量")


def evaluate_scenario(sc: dict, index: int, template: str,
                      contracts: dict[str, Contract],
                      contract_dicts: dict[str, dict]) -> list[dict]:
    """对单组场景产出问题列表。"""
    issues: list[dict] = []
    name = sc.get("name") or f"场景 {index + 1}"

    locale = sc.get("locale", "en")
    if not isinstance(locale, str) or not locale.strip():
        issues.append(_issue(
            "invalid_scenario", SEVERITY_ERROR,
            f"场景「{name}」的 locale 必须是非空字符串", None,
            sc, index, template))
        return issues  # locale 非法时其余检查无意义
    variables = sc.get("variables", {})
    if not isinstance(variables, dict):
        issues.append(_issue(
            "invalid_scenario", SEVERITY_ERROR,
            f"场景「{name}」的 variables 必须是对象", None,
            sc, index, template))
        return issues

    for var_name in sorted(contracts, key=lambda n: contracts[n].first_pos):
        c = contracts[var_name]
        cd = contract_dicts[var_name]
        pos = c.first_pos
        if var_name not in variables:
            if c.unconditional:
                issues.append(_issue(
                    "missing_required", SEVERITY_ERROR,
                    f"必需变量 '{var_name}' 缺失（在模板顶层使用）",
                    var_name, sc, index, template, pos))
            else:
                guard = c.guards[0] if c.guards else "某分支"
                issues.append(_issue(
                    "missing_conditional", SEVERITY_WARNING,
                    f"条件变量 '{var_name}' 缺失（仅在分支中使用，如 {guard}）",
                    var_name, sc, index, template, pos))
            continue

        value = variables[var_name]
        resolved = cd["resolved_type"]
        conflict = cd["type_conflict"]

        # 数字约束（number / plural / selectordinal）
        needs_number = resolved == "number" or conflict
        if needs_number and not _is_finite_number(value):
            if c.has_plural:
                issues.append(_issue(
                    "invalid_plural_value", SEVERITY_ERROR,
                    f"变量 '{var_name}' 用于 plural/selectordinal，必须是数字，"
                    f"当前值 {_preview(value)}（{_value_type_name(value)}）无法解析为有限数字",
                    var_name, sc, index, template, pos, value))
            else:
                issues.append(_issue(
                    "type_conflict", SEVERITY_ERROR,
                    f"变量 '{var_name}' 类型冲突：契约要求数字，"
                    f"当前值 {_preview(value)}（{_value_type_name(value)}）",
                    var_name, sc, index, template, pos, value))
        elif (resolved in ("string", "any") and not conflict
              and isinstance(value, (dict, list))):
            issues.append(_issue(
                "type_conflict", SEVERITY_ERROR,
                f"变量 '{var_name}' 类型冲突：契约要求 {_expected_text(cd)}，"
                f"当前值 {_preview(value)}（{_value_type_name(value)}）不是标量",
                var_name, sc, index, template, pos, value))

        # select 分支键校验（数字 / 布尔会按渲染规则转字符串路由）
        if c.has_select and not isinstance(value, (dict, list)):
            key = _str_value(value)
            for u in c.usages:
                if u.kind != "select" or u.select is None or u.select["has_other"]:
                    continue
                if key not in u.select["selectors"]:
                    issues.append(_issue(
                        "invalid_selector", SEVERITY_ERROR,
                        f"select 变量 '{var_name}' 的值 {_preview(value)} "
                        f"不匹配任何分支（{ '、'.join(u.select['selectors']) }），"
                        "且该节点没有 other 兜底分支",
                        var_name, sc, index, template, u.pos, value))

    # 样例提供但模板未使用的变量
    for extra in sorted(set(variables) - set(contracts)):
        issues.append(_issue(
            "unused_variable", SEVERITY_INFO,
            f"变量 '{extra}' 已提供但模板未使用",
            extra, sc, index, template, None, variables[extra]))

    return issues


# ---------------------------------------------------------------------------
# 顶层审计
# ---------------------------------------------------------------------------

def audit_contract(template: str, scenarios: list[dict]) -> dict:
    """推导契约并批量审计场景，返回按变量聚合的结果与 JSON Schema 草案。"""
    template = template or ""
    scenarios = scenarios or []

    parser_errors: list[dict] = []
    parts: list = []
    if len(template) > MAX_TEMPLATE_LEN:
        parser_errors.append({
            "message": f"模板过长（>{MAX_TEMPLATE_LEN} 字符）",
            "pos": 0, "length": 1, "line": 1, "col": 1,
            "severity": "error"})
    else:
        parser = Parser(template)
        parts = parser.parse()
        parser_errors = list(parser.errors)

    contracts = derive_contracts(template, parts)
    ordered = sorted(contracts, key=lambda n: contracts[n].first_pos)
    contract_dicts = {n: _contract_dict(n, contracts[n], template)
                      for n in ordered}
    contract_items = [contract_dicts[n] for n in ordered]
    json_schema = build_json_schema(contract_items)

    # 模板内类型冲突（与具体场景无关）
    template_issues: list[dict] = []
    for n in ordered:
        cd = contract_dicts[n]
        if cd["type_conflict"]:
            num_at = next((u for u in contracts[n].usages
                           if u.constraint == "number"), None)
            str_at = next((u for u in contracts[n].usages
                           if u.constraint == "string"), None)
            template_issues.append(_issue(
                "template_type_conflict", SEVERITY_ERROR,
                f"变量 '{n}' 在模板中的类型要求冲突："
                f"{KIND_LABELS.get(num_at.kind, num_at.kind)}（第 "
                f"{_line_col(template, num_at.pos)[0]} 行）要求数字，"
                f"{KIND_LABELS.get(str_at.kind, str_at.kind)}（第 "
                f"{_line_col(template, str_at.pos)[0]} 行）要求字符串",
                n, None, None, template, cd["first_pos"]))

    # 逐场景校验
    all_issues: list[dict] = []
    scenario_entries: list[dict] = []
    for idx, sc in enumerate(scenarios):
        sc_issues = evaluate_scenario(sc, idx, template, contracts, contract_dicts)
        all_issues.extend(sc_issues)
        n_err = sum(1 for i in sc_issues if i["severity"] == SEVERITY_ERROR)
        n_warn = sum(1 for i in sc_issues if i["severity"] == SEVERITY_WARNING)
        n_info = sum(1 for i in sc_issues if i["severity"] == SEVERITY_INFO)
        scenario_entries.append({
            "index": idx,
            "id": sc.get("id", idx + 1),
            "name": sc.get("name") or f"场景 {idx + 1}",
            "locale": sc.get("locale", "en"),
            "status": "failed" if n_err else "passed",
            "error_count": n_err,
            "warning_count": n_warn,
            "info_count": n_info,
            "issues": sc_issues,
        })

    # -- 按变量聚合（含模板未使用的额外变量） --
    extra_names = sorted({i["variable"] for i in all_issues
                          if i["code"] == "unused_variable"})
    var_entries: list[dict] = []
    for n in ordered:
        var_entries.append(_aggregate_variable(
            contract_dicts[n], all_issues, template, in_template=True,
            template_issue=next((i for i in template_issues
                                 if i["variable"] == n), None)))
    for n in extra_names:
        var_entries.append(_aggregate_variable(
            {"name": n, "required": False, "required_kind": None,
             "guards": [], "resolved_type": None, "allowed_types": [],
             "type_conflict": False, "usage_kinds": [], "usage_labels": [],
             "select": None,
             "first_pos": None, "first_line": None, "first_col": None},
            all_issues, template, in_template=False, template_issue=None))

    summary = {
        "variables_total": len(ordered),
        "required_total": sum(1 for c in contract_items if c["required"]),
        "conditional_total": sum(
            1 for c in contract_items if c["required_kind"] == "conditional"),
        "type_conflicts_in_template": len(template_issues),
        "scenarios_total": len(scenario_entries),
        "scenarios_passed": sum(
            1 for s in scenario_entries if s["status"] == "passed"),
        "scenarios_failed": sum(
            1 for s in scenario_entries if s["status"] == "failed"),
        "issues_total": len(all_issues),
        "errors": sum(1 for i in all_issues if i["severity"] == SEVERITY_ERROR),
        "warnings": sum(1 for i in all_issues
                        if i["severity"] == SEVERITY_WARNING),
        "infos": sum(1 for i in all_issues if i["severity"] == SEVERITY_INFO),
        "missing_total": sum(
            1 for i in all_issues
            if i["code"] in ("missing_required", "missing_conditional")),
        "type_conflict_total": sum(
            1 for i in all_issues if i["code"] == "type_conflict"),
        "invalid_plural_total": sum(
            1 for i in all_issues if i["code"] == "invalid_plural_value"),
        "invalid_selector_total": sum(
            1 for i in all_issues if i["code"] == "invalid_selector"),
    }

    return {
        "ok": True,
        "template_ok": not parser_errors,
        "errors": parser_errors,
        "summary": summary,
        "contracts": contract_items,
        "template_issues": template_issues,
        "scenarios": scenario_entries,
        "variables": var_entries,
        "issues": all_issues,
        "json_schema": json_schema,
    }


def _aggregate_variable(cd: dict, all_issues: list[dict], template: str,
                        in_template: bool,
                        template_issue: dict | None) -> dict:
    """把同一变量在各场景中的问题按 code 聚合，记录首次出现的场景。"""
    groups_map: dict[str, dict] = {}
    for issue in all_issues:
        if issue["variable"] != cd["name"]:
            continue
        g = groups_map.get(issue["code"])
        ref = dict(issue["scenario"] or {})
        occ = {"scenario": ref, "message": issue["message"]}
        if "value" in issue:
            occ["value"] = issue["value"]
        if g is None:
            groups_map[issue["code"]] = {
                "code": issue["code"],
                "severity": issue["severity"],
                "count": 1,
                "first": ref,
                "message": issue["message"],
                "occurrences": [occ],
            }
        else:
            g["count"] += 1
            g["occurrences"].append(occ)

    def group_order(g):
        rank = {SEVERITY_ERROR: 0, SEVERITY_WARNING: 1, SEVERITY_INFO: 2}
        return rank.get(g["severity"], 3)

    groups = sorted(groups_map.values(), key=group_order)
    return {
        "in_template": in_template,
        "contract": cd,
        "template_issue": template_issue,
        "issue_total": sum(g["count"] for g in groups),
        "groups": groups,
    }
