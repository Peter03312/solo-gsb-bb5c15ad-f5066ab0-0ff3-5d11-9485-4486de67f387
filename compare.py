"""版本对比：新旧 ICU 模板的语义对齐、结构差异与场景回放。

对齐不依赖解析器分配的 n1/n2 节点序号，而是为每个分支节点构造
语义路径：沿嵌套父链拼接「变量名:分支类型@选择器」，同级同名同类
节点按出现次序加 [k] 后缀消歧，例如：

    gender:select@male/count:plural[2]@one

差异类型：
- added     新增（分支或整个节点仅存在于新版）
- removed   删除（分支或整个节点仅存在于基线版）
- condition 条件变化（offset、分支类型、=N 精确匹配值变化）
- render    渲染文本变化（同一语义分支的消息文本被修改）

保存的场景会在新旧两版分别回放；单侧解析或运行失败只记录该侧错误，
不阻断另一侧与其余场景。
"""
from __future__ import annotations

from icu import (
    MAX_TEMPLATE_LEN, Arg, FmtArg, Hash, Parser, Plural, Select, Text,
    analyze,
)

MAX_EXCERPT = 160  # 变更条目携带的模板片段长度上限


def _kind_of(node) -> str:
    return node.kind if isinstance(node, Plural) else "select"


def _fmt_off(v: float) -> str:
    return "%g" % v


def _side(template: str, pos: int, end: int) -> dict:
    """变更一侧的定位信息：起止位置 + 模板片段。"""
    pos = max(0, min(len(template), pos))
    end = max(pos, min(len(template), end))
    text = template[pos:end]
    if len(text) > MAX_EXCERPT:
        text = text[:MAX_EXCERPT] + "…"
    return {"pos": pos, "end": end, "text": text}


# ---------------------------------------------------------------------------
# 语义路径
# ---------------------------------------------------------------------------

def collect_nodes(parts: list) -> list[dict]:
    """先序收集全部分支节点及其语义路径。

    返回 [{path, node, kind, label, loose}]：
    - path  完整语义路径，如 gender:select@male/count:plural
    - label 节点自身的「变量:类型[次序]」标签
    - loose 忽略分支类型的松散键（用于 plural ↔ selectordinal 类型变化配对）
    """
    out: list[dict] = []

    def walk(frag: list, chain: tuple):
        counts: dict = {}
        for node in frag:
            if not isinstance(node, (Plural, Select)):
                continue
            kind = _kind_of(node)
            key = (node.name, kind)
            counts[key] = counts.get(key, 0) + 1
            occ = counts[key]
            label = f"{node.name}:{kind}" + (f"[{occ}]" if occ > 1 else "")
            path = "/".join([*(f"{lab}@{sel}" for lab, sel in chain), label])
            out.append({
                "path": path,
                "node": node,
                "kind": kind,
                "label": label,
                "loose": tuple((lab.split(":")[0], sel) for lab, sel in chain)
                         + ((node.name,),),
            })
            for b in node.branches:
                walk(b.message, chain + ((label, b.selector),))

    walk(parts, ())
    return out


def _branch_entries(node) -> list[tuple[str, object]]:
    """节点的分支列表，重复选择器按出现次序加 [k] 后缀消歧。"""
    counts: dict = {}
    entries = []
    for b in node.branches:
        counts[b.selector] = counts.get(b.selector, 0) + 1
        occ = counts[b.selector]
        key = b.selector if occ == 1 else f"{b.selector}[{occ}]"
        entries.append((key, b))
    return entries


def _shallow_signature(message: list) -> list:
    """消息片段的浅层签名：只含文本与变量，跳过嵌套分支节点。

    嵌套节点的增删与内部变化由它自己的语义路径单独报告，
    这里跳过可避免在父级上重复出现。
    """
    sig = []
    for node in message:
        if isinstance(node, Text):
            sig.append(("t", node.text))
        elif isinstance(node, Arg):
            sig.append(("arg", node.name))
        elif isinstance(node, FmtArg):
            sig.append(("fmt", node.name, node.fmt, node.style))
        elif isinstance(node, Hash):
            sig.append(("hash",))
    return sig


# ---------------------------------------------------------------------------
# 结构差异
# ---------------------------------------------------------------------------

def _node_change(type_: str, info: dict, template: str) -> dict:
    node = info["node"]
    n_br = len(node.branches)
    verb = "新增" if type_ == "added" else "删除"
    return {
        "type": type_,
        "level": "node",
        "path": info["path"],
        "variable": node.name,
        "kind": info["kind"],
        "selector": None,
        "detail": f"{verb}分支节点 {info['label']}（{n_br} 个分支）",
        "old": _side(template, node.pos, node.end) if type_ == "removed" else None,
        "new": _side(template, node.pos, node.end) if type_ == "added" else None,
    }


def _branch_change(type_: str, node_info: dict, branch, template: str,
                   side: str, detail: str) -> dict:
    node = node_info["node"]
    return {
        "type": type_,
        "level": "branch",
        "path": f"{node_info['path']}@{branch.selector}",
        "variable": node.name,
        "kind": node_info["kind"],
        "selector": branch.selector,
        "detail": detail,
        "old": _side(template, branch.pos, branch.end) if side == "old" else None,
        "new": _side(template, branch.pos, branch.end) if side == "new" else None,
    }


def _diff_branches(o_info: dict, n_info: dict,
                   old_template: str, new_template: str,
                   changes: list):
    """比较一对已对齐节点的分支集合。"""
    o_entries = _branch_entries(o_info["node"])
    n_entries = _branch_entries(n_info["node"])
    o_map = dict(o_entries)
    n_map = dict(n_entries)
    old_only = [(k, b) for k, b in o_entries if k not in n_map]
    new_only = [(k, b) for k, b in n_entries if k not in o_map]

    # =N 精确匹配值变化：两侧各恰好剩一个 =N 分支时配对为条件变化
    paired: list[tuple] = []  # (old_branch, new_branch)
    old_exp = [(k, b) for k, b in old_only if k.startswith("=")]
    new_exp = [(k, b) for k, b in new_only if k.startswith("=")]
    if len(old_exp) == 1 and len(new_exp) == 1:
        (ok, ob), (nk, nb) = old_exp[0], new_exp[0]
        paired.append((ob, nb))
        old_only = [(k, b) for k, b in old_only if k != ok]
        new_only = [(k, b) for k, b in new_only if k != nk]
        changes.append({
            "type": "condition",
            "level": "branch",
            "path": f"{n_info['path']}@{nb.selector}",
            "old_path": f"{o_info['path']}@{ob.selector}",
            "variable": n_info["node"].name,
            "kind": n_info["kind"],
            "selector": nb.selector,
            "detail": f"精确匹配选择器从 {ob.selector} 变为 {nb.selector}",
            "old": _side(old_template, ob.pos, ob.end),
            "new": _side(new_template, nb.pos, nb.end),
        })

    for _key, b in old_only:
        changes.append(_branch_change(
            "removed", o_info, b, old_template, "old",
            f"删除分支「{b.selector}」"))
    for _key, b in new_only:
        changes.append(_branch_change(
            "added", n_info, b, new_template, "new",
            f"新增分支「{b.selector}」"))

    # 已配对的分支（含 =N 配对）比较浅层签名 → 渲染文本变化
    matched = [(ob, nb) for k, ob in o_entries
               for nb in [n_map.get(k)] if nb is not None]
    matched.extend(paired)
    for ob, nb in matched:
        if _shallow_signature(ob.message) != _shallow_signature(nb.message):
            changes.append({
                "type": "render",
                "level": "branch",
                "path": f"{n_info['path']}@{nb.selector}",
                "variable": n_info["node"].name,
                "kind": n_info["kind"],
                "selector": nb.selector,
                "detail": f"分支「{nb.selector}」的渲染文本已修改",
                "old": _side(old_template, ob.pos, ob.end),
                "new": _side(new_template, nb.pos, nb.end),
            })


def _diff_node_pair(o_info: dict, n_info: dict, kind_changed: bool,
                    old_template: str, new_template: str,
                    changes: list):
    o_node, n_node = o_info["node"], n_info["node"]
    if kind_changed:
        changes.append({
            "type": "condition",
            "level": "node",
            "path": n_info["path"],
            "old_path": o_info["path"],
            "variable": n_node.name,
            "kind": n_info["kind"],
            "selector": None,
            "detail": f"分支类型从 {o_info['kind']} 变为 {n_info['kind']}"
                      "（复数类别规则随之改变）",
            "old": _side(old_template, o_node.pos, o_node.end),
            "new": _side(new_template, n_node.pos, n_node.end),
        })
    if isinstance(o_node, Plural) and isinstance(n_node, Plural) \
            and o_node.offset != n_node.offset:
        changes.append({
            "type": "condition",
            "level": "node",
            "path": n_info["path"],
            "variable": n_node.name,
            "kind": n_info["kind"],
            "selector": None,
            "detail": f"plural 偏移量 offset 从 {_fmt_off(o_node.offset)} "
                      f"变为 {_fmt_off(n_node.offset)}",
            "old": _side(old_template, o_node.pos, o_node.end),
            "new": _side(new_template, n_node.pos, n_node.end),
        })
    _diff_branches(o_info, n_info, old_template, new_template, changes)


def diff_parts(old_parts: list, new_parts: list,
               old_template: str, new_template: str) -> list[dict]:
    """对两棵 AST 做语义对齐并产出变更列表。"""
    changes: list[dict] = []

    # 顶层文本（不属于任何分支的部分）变化
    if _shallow_signature(old_parts) != _shallow_signature(new_parts):
        changes.append({
            "type": "render",
            "level": "message",
            "path": "（顶层文本）",
            "variable": None,
            "kind": None,
            "selector": None,
            "detail": "顶层文本（不属于任何分支的部分）已修改",
            "old": _side(old_template, 0, len(old_template)),
            "new": _side(new_template, 0, len(new_template)),
        })

    old_infos = collect_nodes(old_parts)
    new_infos = collect_nodes(new_parts)
    old_map = {i["path"]: i for i in old_infos}
    new_map = {i["path"]: i for i in new_infos}

    pairs: list[tuple] = []  # (old_info, new_info, kind_changed)
    old_rest: list[dict] = []
    for path, o in old_map.items():
        n = new_map.get(path)
        if n is not None:
            pairs.append((o, n, False))
        else:
            old_rest.append(o)
    new_rest = [n for p, n in new_map.items() if p not in old_map]

    # 分支类型变化配对：松散键（忽略类型）相同且两侧各唯一时视为同一节点
    paired_new: set[int] = set()
    still_old: list[dict] = []
    for o in old_rest:
        same_old = [x for x in old_rest if x["loose"] == o["loose"]]
        cands = [n for n in new_rest
                 if n["loose"] == o["loose"] and id(n) not in paired_new]
        if len(same_old) == 1 and len(cands) == 1:
            pairs.append((o, cands[0], True))
            paired_new.add(id(cands[0]))
        else:
            still_old.append(o)
    still_new = [n for n in new_rest if id(n) not in paired_new]

    # 整棵新增/删除的节点：只报告最外层，后代节点随之整体增删
    removed_paths = {o["path"] for o in still_old}
    added_paths = {n["path"] for n in still_new}

    def covered_by(path: str, ancestors: set) -> bool:
        # 后代路径 = 祖先路径 + "@选择器/..."，据此判断祖先是否已被整体增删
        return any(path.startswith(ap + "@") for ap in ancestors)

    for o in still_old:
        if not covered_by(o["path"], removed_paths):
            changes.append(_node_change("removed", o, old_template))
    for n in still_new:
        if not covered_by(n["path"], added_paths):
            changes.append(_node_change("added", n, new_template))

    for o, n, kind_changed in pairs:
        _diff_node_pair(o, n, kind_changed, old_template, new_template, changes)

    def sort_key(c):
        side = c.get("new") or c.get("old") or {}
        return side.get("pos", 0)

    changes.sort(key=sort_key)
    for i, c in enumerate(changes):
        c["id"] = i + 1
    return changes


# ---------------------------------------------------------------------------
# 场景回放（两版分别运行，单侧失败互不影响）
# ---------------------------------------------------------------------------

def _run_one(template: str, sc: dict) -> dict:
    locale = sc.get("locale", "en")
    variables = sc.get("variables", {})
    if not isinstance(locale, str) or not locale.strip():
        return {"status": "failed", "error": "locale 必须是非空字符串",
                "output": None}
    if not isinstance(variables, dict):
        return {"status": "failed", "error": "variables 必须是对象",
                "output": None}
    try:
        r = analyze(template, locale.strip(), variables)
    except Exception as exc:  # 单侧内部异常不应中断其余场景
        return {"status": "failed", "error": f"分析失败：{exc}", "output": None}
    if not r.get("ok"):
        errs = r.get("errors") or []
        return {"status": "failed",
                "error": "；".join(e.get("message", "") for e in errs)
                         or "模板存在错误",
                "output": None}
    return {"status": "passed", "error": None,
            "output": r["main"]["output"],
            "conditions": r["main"]["conditions"]}


def replay_scenarios(old_template: str, new_template: str,
                     scenarios: list[dict]) -> dict:
    """把同一组场景分别在新旧两版模板上运行并比较输出。"""
    items: list[dict] = []
    changed = 0
    for idx, sc in enumerate(scenarios):
        name = str(sc.get("name") or "") or f"场景 {idx + 1}"
        old_res = _run_one(old_template, sc)
        new_res = _run_one(new_template, sc)
        both = old_res["status"] == "passed" and new_res["status"] == "passed"
        diff = both and old_res["output"] != new_res["output"]
        if diff:
            changed += 1
        items.append({
            "id": sc.get("id", idx + 1),
            "name": name,
            "locale": sc.get("locale", "en"),
            "old": old_res,
            "new": new_res,
            "changed": diff,
        })
    return {
        "total": len(items),
        "changed": changed,
        "old_failed": sum(1 for i in items if i["old"]["status"] != "passed"),
        "new_failed": sum(1 for i in items if i["new"]["status"] != "passed"),
        "items": items,
    }


# ---------------------------------------------------------------------------
# 顶层对比
# ---------------------------------------------------------------------------

def compare_templates(old_template: str, new_template: str,
                      scenarios: list[dict] | None = None) -> dict:
    """对比基线（old）与当前（new）两版模板。

    两侧模板各自独立解析：解析错误记录在该侧，仍基于可恢复的 AST 计算
    差异；场景在两版上分别回放，单侧失败不影响其余场景。
    """
    old_template = old_template or ""
    new_template = new_template or ""
    scenarios = scenarios or []

    sides: dict = {}
    parts: dict = {}
    for side, tpl in (("old", old_template), ("new", new_template)):
        if len(tpl) > MAX_TEMPLATE_LEN:
            sides[side] = {"ok": False, "errors": [{
                "message": f"模板过长（>{MAX_TEMPLATE_LEN} 字符）",
                "pos": 0, "length": 1, "line": 1, "col": 1,
                "severity": "error"}]}
            parts[side] = None
            continue
        parser = Parser(tpl)
        parts[side] = parser.parse()
        sides[side] = {"ok": not parser.errors, "errors": parser.errors}

    changes: list[dict] = []
    if parts["old"] is not None and parts["new"] is not None:
        changes = diff_parts(parts["old"], parts["new"],
                             old_template, new_template)

    summary = {"added": 0, "removed": 0, "condition": 0, "render": 0}
    for c in changes:
        summary[c["type"]] += 1
    summary["total"] = len(changes)

    return {
        "ok": True,
        "old": sides["old"],
        "new": sides["new"],
        "changes": changes,
        "summary": summary,
        "scenarios": replay_scenarios(old_template, new_template, scenarios),
    }
