# ICU 文案分支预览器（ICU MessageFormat Branch Previewer）

一个 Flask 网页工具：粘贴 ICU MessageFormat 模板、填写变量样例，即可
解析嵌套的 `plural` / `select` / `selectordinal` 语法，按语言环境（locale）
渲染所有可达分支并列出每个分支的触发条件。

## 功能

- **全分支预览**：枚举模板的所有可达分支路径（含嵌套），逐条渲染并标注触发条件
  （如 `count = 0（精确匹配）`、`count 的复数类别为 "one"（示例：count = 1）`）。
- **CLDR 复数规则**：按语言环境计算复数类别（cardinal 基于 Babel/CLDR 数据，
  ordinal 内置常见语言规则），同一变量在多个分支间保证路径自洽。
- **行内错误提示**：括号不配对、未知格式类型、缺少 `other` 分支、重复选择器、
  非法复数类别、变量类型错误（如 plural 变量给了字符串）等，均在编辑器中
  以红色波浪线标出，并在下方列出带行列号的说明，点击可定位。
- **未覆盖分支标记**：对照当前变量样例，标记每个分支 / 每条路径为
  「已覆盖 / 未覆盖 / 不可达」（不可达 = 该复数类别在当前语言环境下不存在，
  或被 `=N` 显式分支抢占）。
- **场景集回归**：为当前模板保存多组带名称的 locale 与变量样例（保存在浏览器
  本地），一键批量调用分析接口并合并每条可达路径的覆盖状态。面板显示总路径数、
  已覆盖 / 未覆盖数量与覆盖率进度条；点击未覆盖项可定位到对应路径的触发条件；
  支持单个场景重跑、删除与「载入编辑器」，并可导出包含场景定义与汇总结果的 JSON。
  单个场景失败时保留错误信息并继续运行其余场景。
- **版本对比**：把当前模板设为基线（保存在浏览器本地）后继续编辑，随时与基线
  对比。后端按**语义路径**对齐新旧模板——由变量名、分支类型、选择器及嵌套父链
  拼接而成（如 `gender:select@male/count:plural@one`），不依赖解析器分配的
  n1/n2 节点序号，因此在模板前面插入新节点也不会造成大面积误报。差异分为
  新增、删除、条件变化（offset / 分支类型 / `=N` 精确匹配值）与渲染文本变化
  四类，界面可按类型筛选，点击变更条目会在编辑器中选中当前版片段、并在基线
  视图中高亮基线片段。对比时所有已保存场景会在新旧两版分别回放并比较输出；
  单侧解析或运行失败只保留该侧错误，不阻断其余场景。
- **变量管理**：从模板一键提取变量；数值 / 布尔自动识别。
- **变量契约审计**：从模板 AST 推导每个变量在简单占位、`number`、`plural`、
  `select`、`selectordinal`（含 plural 内的 `#`）中的**允许类型与必需性**
  （顶层使用为必需，仅在分支内使用为条件必需并记录守卫路径）；同一变量被
  数字用法与字符串用法同时约束时报告模板内类型冲突。支持**导入多组场景样例**
  （场景集导出文件、场景数组或 `{"scenarios": [...]}`），批量报告缺失变量、
  类型冲突、无效复数值、无匹配的 select 键（含非法场景），并给出每个问题在
  模板与场景中的**首次出现位置**；结果**按变量聚合**（问题类型、出现次数、
  首次场景与全部出现场景），同时生成可一键复制的 **JSON Schema 草案**
  （draft-07，无 `other` 兜底的 select 输出 `enum`），并可导出完整审计 JSON。
- **复制与导出**：复制单条或全部渲染结果；一键导出包含模板、变量与完整
  分析结果的 JSON 文件；场景集可单独导出为回归报告 JSON。

## 快速开始

### 方式一：Docker Compose（推荐）

```bash
docker compose up --build
```

### 方式二：Docker

```bash
docker build -t icu-branch-previewer .
docker run --rm -p 5000:5000 icu-branch-previewer
```

### 方式三：本地运行

```bash
pip install -r requirements.txt
python app.py            # 或：gunicorn --bind 0.0.0.0:5000 app:app
```

### 访问

启动后在浏览器打开：**http://localhost:5000**

端口可通过环境变量 `PORT` 修改（Compose 中需同步调整 `ports` 映射）。

## 使用说明

1. 在左侧粘贴 ICU MessageFormat 模板，例如：

   ```
   {gender, select, male{He} female{She} other{They}} has {count, plural, =0{no new messages} one{# new message} other{# new messages}}.
   ```

2. 选择语言环境（如 `en`、`zh-Hans`、`ru`、`ar`），并填写变量样例
   （或点「从模板提取变量」）。
3. 右侧实时展示：
   - **样例渲染结果**：当前变量取值下的输出及其命中的分支条件；
   - **分支节点**：每个 plural/select 的全部分支、触发条件与覆盖状态；
   - **可达分支路径**：所有分支组合的渲染结果，未覆盖的以橙色标记。
4. 点击「复制」复制结果，点击「导出 JSON」下载完整分析。

### 场景集回归

页面底部的「场景集回归」面板用于多组样例的批量回归：

1. 在左侧调好 locale 与变量后，点 **＋ 保存当前为场景**，并为场景命名
   （名称可随时就地修改，场景按模板内容保存在浏览器 `localStorage`，
   切换模板时自动加载该模板对应的场景集）。
2. 重复步骤 1 保存多组场景（如 `en` 的 0/1/其他、`ru` 的 few/many 等）。
3. 点 **▶ 运行全部场景**：后端逐个调用分析逻辑并返回合并结果；
   单个场景失败（模板错误、变量类型错误等）会在行内标红显示错误，
   其余场景继续运行，不影响合并。
4. 右侧「合并路径覆盖」显示总路径数、已覆盖 / 未覆盖数量、覆盖率进度条，
   以及每条路径由哪个场景覆盖；**点击任意未覆盖路径**会滚动定位到上方
   「可达分支路径」中的对应卡片并高亮（其触发条件以 chips 展示）。
5. 每个场景支持 **载入**（把 locale/变量填回编辑器）、**重跑**（仅重跑
   该场景并更新合并）、**删除**。
6. 点 **导出场景 JSON** 下载 `icu-scenarios.json`，包含模板、全部场景定义、
   每个场景的通过 / 失败状态与错误、路径覆盖汇总与每条路径在各场景下的输出。

> 单次运行最多 50 个场景；路径合并以模板中分支节点的路由序列为稳定 key，
> 因此不同 locale 下枚举到的路径会取并集。

### 版本对比

页面底部的「版本对比」面板用于基线模板与当前编辑内容的对照：

1. 调好作为基准的模板后，点 **设为基线**——基线保存在浏览器 `localStorage`
   （键 `icu-preview-baseline-v1`），刷新页面仍在；保存时会**快照当时的场景集**
   （继续编辑后场景列表会切换到新模板对应的集合，快照保证对比时回归场景不丢），
   基线状态行会实时提示「与当前一致 / 当前已修改」。
2. 继续编辑模板，然后点 **与基线对比**。后端把新旧模板各自解析为 AST，
   按语义路径（变量名 `:分支类型@选择器` 沿嵌套父链拼接，同级同名同类节点
   按出现次序加 `[k]` 后缀）对齐，返回四类变更：
   - **新增 / 删除**：仅存在于一侧的分支或整个分支节点（嵌套节点整体增删时
     只报告最外层）；
   - **条件变化**：plural 的 `offset` 改变、`plural ↔ selectordinal` 类型
     切换、`=N` 精确匹配值改变；
   - **文本变化**：同一语义分支（或顶层文本）的消息文本被修改。
3. 变更列表顶部可按类型筛选（全部 / 新增 / 删除 / 条件变化 / 文本变化）；
   **点击任意变更条目**会在上方编辑器中选中当前版对应片段，并在右侧
   「基线模板」只读视图中高亮基线片段。
4. 对比时基线快照的场景与当前场景集合并去重后，会在新旧两版**分别回放**：
   每个场景显示两版各自的渲染输出或错误，输出不同会标记「输出有变化」。
   某一侧解析失败（如基线版本身有语法错误）或运行失败（如变量类型错误）
   只在该侧保留错误，其余场景照常运行。
5. 点 **清除基线** 删除已保存的基线与对比结果。

> 两版模板各自独立解析：即使一侧存在语法错误，也会基于可恢复的 AST 计算
> 差异，并在面板顶部保留该侧的错误提示。

### 变量契约审计

页面底部的「变量契约审计」面板用于推导变量类型契约并批量审计场景样例：

1. 点 **导入场景 JSON** 选择场景文件（支持「导出场景 JSON」的完整导出、
   场景数组 `[{name, locale, variables}, ...]`，或 `{"scenarios": [...]}`）；
   也可点 **用已保存场景** 直接载入上方「场景集回归」中保存的场景。
2. 点 **运行审计**：后端解析模板 AST，为每个变量推导：
   - **允许类型**：简单占位 → 任意标量（字符串 / 数字 / 布尔）；`number`、
     `plural`、`selectordinal`（及 plural 内的 `#`）→ 数字；`select` → 字符串；
     `date` / `time` / `spellout` / `ordinal` / `duration` → 任意标量；
   - **必需性**：不处于任何分支守卫下的变量为**必需**；仅出现在分支内的为
     **条件必需**，并记录守卫的语义路径（如 `gender:select@male/count:plural@one`）；
   - **select 分支键**：收集允许的键，并标注是否缺少 `other` 兜底；
   - **模板内类型冲突**：同一变量同时被要求为数字（number/plural）与字符串
     （select）时直接标红。
3. 审计结果分两栏：
   - 左侧 **审计场景**：每个场景通过 / 失败，问题按 缺失必需变量 / 缺失条件
     变量 / 类型冲突 / 无效复数值 / 无匹配 select 键 / 未使用变量 着色展示，
     点击问题可定位到模板首次出现位置；
   - 右侧 **按变量聚合**：每个变量一张卡片，显示类型、必需性、全部用法、
     select 键、分支守卫，以及该变量在各场景中的问题聚合（类型、次数、
     首次出现的场景与消息、全部涉及的场景）；下方为 **JSON Schema 草案**，
     点 **复制 Schema** 复制 draft-07 草案（无 `other` 兜底的 select 生成
     `enum`，条件必需信息写入 `description`，`additionalProperties: true`）。
4. 模板在审计后被修改时，面板顶部提示结果已过期，重新运行即可。
5. 点 **导出审计 JSON** 下载 `icu-contract-audit.json`，包含模板、场景定义、
   汇总、变量契约、按变量聚合、问题明细与 JSON Schema 草案。

> 说明：数字字符串（如 `"2"`）按渲染器规则可解析为数字，不会报冲突；
> 布尔 / 对象 / 数组无法作为 plural 数值，会计为无效复数值。单次最多
> 审计 50 个场景。

## HTTP API

`POST /api/preview`

```json
{
  "template": "{count, plural, one{# item} other{# items}}",
  "locale": "en",
  "variables": {"count": 3}
}
```

响应包含 `errors / warnings / infos`（带行列号）、`main`（样例渲染）、
`nodes`（分支结构与覆盖状态）、`paths`（全部可达路径，含稳定标识 `key`）、
`stats`（路径总数、覆盖数、是否截断）以及编辑器高亮用的 `tokens`。

`POST /api/scenarios/run` — 场景集批量回归（单场景失败不中断整批）：

```json
{
  "template": "{g, select, male{He} other{They}} {n, plural, one{#} other{#s}}",
  "scenarios": [
    {"id": "s1", "name": "男-1", "locale": "en", "variables": {"g": "male", "n": 1}},
    {"id": "s2", "name": "其他-2", "locale": "en", "variables": {"g": "x", "n": 2}}
  ]
}
```

响应包含 `summary`（场景通过 / 失败数，路径总数、已覆盖 / 未覆盖数）、
`scenarios`（每个场景的 `status`、`error` 与完整 analyze `result`）以及
`paths`（跨场景合并后的路径，含 `key`、`conditions`、`covered`、
`covered_by` 与各场景输出 `outputs`）。请求体非法或场景数超过 50 返回 400。

`POST /api/compare` — 版本对比（语义对齐新旧模板，场景双版回放）：

```json
{
  "old_template": "{g, select, male{He} other{They}}",
  "new_template": "{g, select, male{He} female{She} other{They}}",
  "scenarios": [
    {"id": "s1", "name": "男性", "locale": "en", "variables": {"g": "male"}}
  ]
}
```

- `old_template` / `new_template` 必填且必须是字符串；`scenarios` 可省略，
  上限 50 个，格式同 `/api/scenarios/run`。
- 响应字段：
  - `old` / `new`：两侧各自的解析结果（`ok` 与带行列号的 `errors`），
    一侧有语法错误不影响另一侧，差异仍基于可恢复的 AST 计算；
  - `changes`：变更列表，每项含 `type`（`added` / `removed` / `condition` /
    `render`）、`level`（`node` / `branch` / `message`）、语义路径 `path`
    （如 `gender:select@male/count:plural@one`）、`detail` 描述，以及
    `old` / `new` 两侧的定位信息（`pos` / `end` / `text` 片段，无该侧时为
    `null`）；
  - `summary`：各类型变更计数与 `total`；
  - `scenarios`：场景在两版的回放结果，`items` 中每个场景含 `old` / `new`
    两侧的 `status` / `output` / `error` 与输出是否变化的 `changed` 标记，
    单侧失败不阻断其余场景。

`POST /api/contract-audit` — 变量契约审计（推导类型契约 + 批量审计场景）：

```json
{
  "template": "{gender, select, male{He} other{They}} {count, plural, one{#} other{#s}}",
  "scenarios": [
    {"name": "正常", "locale": "en", "variables": {"gender": "male", "count": 1}},
    {"name": "缺变量", "locale": "en", "variables": {}},
    {"name": "坏复数值", "locale": "en", "variables": {"gender": "male", "count": "many"}}
  ]
}
```

- `template` 必填且必须是字符串；`scenarios` 可省略或为空数组（此时只推导契约
  与 Schema），上限 50 个；非法请求体 / 非字符串模板 / 非数组场景返回 400。
- 响应字段：
  - `template_ok` / `errors`：模板是否可解析及带行列号的语法错误（有错误时
    契约仍基于可恢复 AST 推导）；
  - `contracts`：按首次出现顺序排列的变量契约，每项含 `name`、`required` /
    `required_kind`（`required` / `conditional`）、`guards`（守卫语义路径）、
    `resolved_type`（`number` / `string` / `any` / `conflict`）、
    `allowed_types`、`type_conflict`、`usage_kinds` / `usage_labels`、
    `select.selectors` / `select.has_other`、首次出现位置
    `first_pos` / `first_line` / `first_col`；
  - `template_issues`：模板内类型冲突（`template_type_conflict`），与场景无关；
  - `scenarios`：每个场景的 `status`（`passed` / `failed`）、各严重级别计数与
    `issues` 明细；
  - `issues`：全部场景问题的扁平列表，`code` 取值为 `missing_required` /
    `missing_conditional` / `type_conflict` / `invalid_plural_value` /
    `invalid_selector` / `unused_variable` / `invalid_scenario`，每项含
    `severity`（`error` / `warning` / `info`）、`message`、`variable`、
    `scenario`（首次出现的场景：`index` / `id` / `name`）以及模板首次出现
    位置 `template_pos` / `template_line` / `template_col`；
  - `variables`：**按变量聚合**的结果，每项含 `contract`、`template_issue`
    （模板未使用的额外变量 `in_template: false`）与 `groups`，每个分组含
    `code`、`severity`、`count`、`first`（首次出现场景）及 `occurrences`
    （每次出现的场景与消息）；
  - `json_schema`：draft-07 草案，必需变量进入 `required`，无 `other` 兜底的
    select 生成 `enum`，模板冲突变量生成 `oneOf`，条件必需与分支键写入
    `description`，并允许额外属性（`additionalProperties: true`）；
  - `summary`：变量 / 必需 / 条件数量、模板冲突数、场景通过失败数、各问题
    类型计数（`missing_total` / `type_conflict_total` /
    `invalid_plural_total` / `invalid_selector_total` 等）。

## 支持的语法

| 语法 | 示例 |
| --- | --- |
| 简单变量 | `{name}` |
| 数字格式化 | `{n, number}`、`{n, number, percent}` |
| 复数 | `{n, plural, =0{none} one{# item} other{# items}}` |
| 序数 | `{pos, selectordinal, one{#st} two{#nd} few{#rd} other{#th}}` |
| 选择 | `{g, select, male{He} female{She} other{They}}` |
| 偏移 | `{n, plural, offset:1 one{you} other{you and # others}}` |
| 转义 | `''` 输出单引号；`'{`、`'}`、`'#` 输出字面符号 |
| 嵌套 | 以上语法可任意嵌套 |

说明：`date` / `time` / `spellout` / `duration` 类型在预览中原样输出变量值；
路径枚举上限为 300 条（超出会标记 `truncated`）。

## 项目结构

```
├── app.py               # Flask 入口（/、/api/preview、/api/scenarios/run、/api/compare、/api/contract-audit）
├── icu.py               # ICU 解析器、渲染器、分支枚举、覆盖分析与场景集合并
├── compare.py           # 版本对比：语义路径对齐、结构差异与场景双版回放
├── contract.py          # 变量契约：AST 类型/必需性推导、场景批量审计与 JSON Schema 草案
├── templates/index.html # 单页界面（含场景集回归、版本对比与变量契约审计面板）
├── static/app.js        # 前端逻辑（高亮、实时分析、场景集、版本对比、契约审计、复制、导出）
├── static/style.css
├── tests/test_icu.py    # ICU 解析/渲染/场景集单元测试
├── tests/test_app.py    # Flask 接口测试
├── tests/test_compare.py# 版本对比与 /api/compare 测试
├── tests/test_contract.py# 变量契约推导与 /api/contract-audit 测试
├── requirements.txt
├── Dockerfile
└── docker-compose.yml
```

## 测试

```bash
pip install pytest
python -m pytest tests/ -q
```
