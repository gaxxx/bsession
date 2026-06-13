# Record → Replay JIT 设计

录制 LLM 的浏览器操作 → 固化成可重放的 TOML 脚本 → 反复调用中自我晋级,
把"贵且灵活的 LLM"换成"便宜确定的脚本",站点改版时自动降级回 LLM。

- **日期**: 2026-06-12
- **状态**: 已批准,待实现计划
- **背景**: 现状只有手写 `run.sh`(确定性脚本,见 `skills/uscis-check/run.sh`)
  和 skill-builder 文档里描述但**未实现**的 YAML runner。本设计提供一条
  TOML 录制/重放/自测/自我晋级的完整路径,与 `run.sh` 并存。

---

## 1. 目标与非目标

### 目标
- 把 LLM 探索网站时的成功操作**自动录制**成确定性的 TOML step 列表。
- 提供**执行器**重放该 TOML,运行期不需要 LLM。
- 内置四种**自测**:跑通、结果与 LLM 一致、站点漂移检测、成功率统计。
- 反复调用中**自我晋级**(分层编译):cold → shadow → verified,漂移则去优化。
- 一个 skill 支持**多个操作**(如 post-article / post-comment),共享登录态。

### 非目标(YAGNI)
- 不做 `loop` / `if` 控制流。需要重复 → 参数传 count、外层多跑几遍。
- 不做翻页/分页抓取。
- 不自动切分 `include` 片段(留给人/Claude 手动提取)。
- 不替换现有 `run.sh` 路径 —— TOML 是并行的第二条固化方式。

---

## 2. 架构与模块

```
lib/
├── primitives.py   (新) 纯原语函数层:nav/find/click/fill/extract/...
│                        返回值或抛异常;无 print、无 sys.exit、无 argparse
├── cli.py          (改) cmd_* 退化为薄壳:调 primitives,负责 print/exit
├── recorder.py     (新) 录制器:把成功的原语调用追加成 TOML step
├── replay.py       (新) 执行器:读 TOML,逐 step 调 primitives,结构化收集结果
├── skilltest.py    (新) 测试器:编译期校验 / 漂移检测 / 成功率统计
└── lifecycle.py    (新) 状态机调度:cold→shadow→verified 晋级与降级
```

### 基础重构(方案 A 的核心)
现状 `cmd_find` / `cmd_extract` 等既做事又 `print`/`sys.exit`,执行器无法复用。
把"做事"抽到 `lib/primitives.py`:

```python
# primitives.py —— 纯逻辑,被 CLI / recorder / replay 共用
def find(profile, pattern) -> str | None       # 返回 ref,找不到返回 None
def extract(profile, regex, max_lines=None, exclude=None) -> list[str]
def click(profile, ref, wait=1.0) -> None       # 失败抛异常
def fill(profile, ref, value) -> None
# nav / type / select / wait / wait_for / bypass 同理
```

`cmd_find` 退化为:调 `primitives.find`,`None` 则 `sys.exit(1)`,否则 `print(ref)`。
**CLI 对外行为完全不变** —— 现有 `run.sh`、`tests/` 全部照常。这是"单一事实源":
CLI、录制器、执行器走同一套原语。

---

## 3. TOML 产物 schema

沿用项目 TOML 习惯(`_bsession_*` 保留前缀),`[[step]]` 数组表示有序步骤。
产物放 `skills/<skill>/recorded/<name>.toml`,与手写的 `forms/` 平级。

```toml
name = "uscis-check"
description = "Check one USCIS case status"
version = "1.0.0"
recorded_at = "2026-06-12T01:30:00Z"
source = "llm-trace"

# ── 自我晋级状态(见 §7)──
state = "cold"            # cold | shadow | verified
shadow_matches = 0        # 连续匹配 LLM 次数
verified = false

[params]                  # 录制器自动从 form 反推
receipt_number = { required = true, secret = false }

[[step]]
action = "nav"
url = "https://egov.uscis.gov/casestatus/mycasestatus.do"
wait = 8

[[step]]
action = "bypass"
kind = "cloudflare"

[[step]]
action = "find"
name = "receipt_input"               # 后续用 {{receipt_input}} 引用
patterns = ["textbox", "text.*receipt"]
# optional = true                    # 找不到则跳过本步及其依赖步(可选)

[[step]]
action = "fill"
ref = "{{receipt_input}}"
value = "{{receipt_number}}"          # 自动参数化:原值匹配 form 字段被替换

[[step]]
action = "find"
name = "submit_btn"
patterns = ["[Cc]heck [Ss]tatus", "button.*[Ss]ubmit"]

[[step]]
action = "click"
ref = "{{submit_btn}}"
wait = 5

[[step]]
action = "extract"
name = "status"
pattern = 'heading "Case ([^"]*)"'
max_lines = 1
exclude = "Status Online"

[result]
status = "{{status}}"

[expected_result]         # 录制时 LLM 那次的结果,供测试②对比
status = "Case Was Approved"

[monitor]                 # 可选
interval = 3600
change_field = "status"
on_drift = "alert"        # alert | llm
[monitor.notify]
webhook = "{{WEBHOOK_URL}}"
```

### 关键设计点
- `action` 一对一映射现有原语:`nav`/`bypass`/`find`/`fill`/`type`/`select`/
  `click`/`extract`/`wait`/`wait_for`/`check`。不发明新动作。
- `find` 存 `name`+`patterns[]`,后续用 `{{name}}` 引用 ref —— 解决 ref 每次变。
- `{{...}}` 统一指代:`[params]` 参数、`find` 产出的 ref、`extract` 产出的值。
- `[params]` 中 `secret = true` 的字段值永不落盘,运行时从 `BSESSION_FORM`/env 注入。
- 轻量条件:`optional = true`(找不到则跳过)、`skip_if_empty = "<name>"`
  (引用的变量为空则跳过本步)。

---

## 4. 多操作站点模型

一个 **skill = 一个站点**,共用一个 profile(cookies 落盘持久化)。多个操作各为
一份录制 TOML:

```
skills/myblog/
├── recorded/
│   ├── _login.toml         # 共享片段:登录,只录一次
│   ├── post-article.toml   # include = ["_login"]
│   └── post-comment.toml   # include = ["_login"]
```

- **`include = ["_login"]`**:执行器把片段的 step 拼到当前操作前面。
- **"只在需要时登录"** 用守卫步:`_login.toml` 首步 `find login_form` 标
  `optional = true`,找不到(已登录)则整段短路;后续 fill 用
  `skip_if_empty = "login_form"`。
- 因共用 profile,登录一次后 cookie 持久化,后续操作天然带会话。

---

## 5. 录制器

- **开关**:`BSESSION_RECORD=<toml路径>`。未设时行为零变化(现有测试/skill 不受影响)。
- **挂点**:`cmd_*` 薄壳里,原语**成功**返回后调 `recorder.append(action, fields)`。
  失败的调用(find 未命中、click 抛异常)**不记录** —— 只固化走通的路径。
- **两个自动转换**:
  1. **值 → 参数引用**:录到 `fill <ref> "WAC123..."` 时回查 `BSESSION_FORM`
     字段值;等于某字段则写 `value = "{{receipt_number}}"` 并登记 `[params]`。
     匹配不到的字面值(如固定 URL)原样保留。
  2. **ref → find 命名引用**:记住裸 ref 来自上一条 `find`,把 find 存成
     `name`+`patterns`(patterns 取那次 find 实际用的正则),后续步用 `{{name}}`。
- **密码安全**:仅命中 `secret=true` 字段才替换;若某填入值匹配不到任何 form
  字段、又疑似密文(长度/字符集启发式),录制器**留空并告警**,绝不写进 TOML。
- **收尾**:skill 跑完写文件头(`name`/`description`/`recorded_at`/`source`/`state="cold"`)。
- **`include` 片段不自动切分**:录完由 Claude 读一遍,把公共前缀(如登录)手动提成
  `_login.toml`(自动切分易切错)。

---

## 6. 执行器与四种测试

### 执行器 `lib/replay.py`
读 TOML → 解析 `include` 拼接 → 逐 step 调 `primitives.*`。维护变量表(`{{...}}`):
`[params]` 注入值、`find` 产出 ref、`extract` 产出值都入表,引用时替换。每步产出
结构化结果:

```python
{"step": 3, "action": "fill", "ok": True, "ms": 120}
{"step": 4, "action": "find", "ok": False, "error": "no match: button.*Submit"}
```

`optional`/`skip_if_empty` 步失败记 `skipped`,不算错。跑完返回
`{ok, steps[], result{}, failed_step}`。这份结构化轨迹是四种测试的共同数据源。

### 四种测试 `lib/skilltest.py`

| 测试 | 时机 | 做法 | 判定 |
|---|---|---|---|
| ① 跑通 | 录完立刻 | 重放一遍 | 所有非 optional step `ok=true`,走到 `[result]` |
| ② 结果与 LLM 一致 | 录完立刻 | 重放结果 vs `[expected_result]` | 字段相等(或匹配指定正则) |
| ③ 漂移检测 | 运行期/monitor | 每次重放看 `failed_step` 与 extract 是否空 | find 失败 / 提取空 → 漂移 |
| ④ 成功率 | 长期累积 | 每次重放追加 `recorded/<name>.stats.jsonl`(时间/ok/failed_step/耗时) | 汇总最近 N 次通过率 |

- **①② 是"编译验证":** 录完不自动信,先重放 + 对比 LLM,过了才标 `verified=true`。
- **漂移回退(③)** 由 `on_drift` 配置:
  - `alert`(默认):发通知 + 标 `verified=false`,停用脚本待人处理;
  - `llm`:回退 LLM 重新探索那段(需 Claude 在场;无人值守的 monitor 通常用 alert)。

---

## 7. 自我晋级生命周期(分层编译)

调度入口 `bsession skill invoke <name>` 按 TOML 头的 `state` 自动选执行方式:

| 状态 | JVM 类比 | 第几次调用 | 行为 |
|---|---|---|---|
| **COLD** | 解释执行 | 第 1 次 | 无脚本 → LLM 探索 + 录制,产出未验证 TOML |
| **SHADOW** | 分层 profiling | 第 2~K 次 | LLM 仍权威;同时影子重放脚本对比结果,一致则 `shadow_matches++`;Claude 顺手清洗轨迹(提 `_login`、收紧 wait)。连续 **K** 次一致 → 升 VERIFIED |
| **VERIFIED** | 已编译热路径 | 第 K+1 次起 | 只跑脚本,不用 LLM;每隔 **M** 次做影子复检 / 漂移检测 |
| (漂移) | 去优化 deopt | 任意 | 脚本失败/提取空 → 降回 COLD/SHADOW,回退 LLM |

- **默认阈值(可配)**:`K = 3`(连续匹配几次才升 verified),`M = 20`(verified 后
  每几次复检 LLM;也支持按时间,如每天一次)。配置项 `_bsession_promote_after`、
  `_bsession_recheck_every`。
- **LLM-in-loop 边界**:COLD/SHADOW 的"LLM 探索"需 Claude 在场 → 自我晋级发生在
  **经 Claude 调用 skill** 时。升到 VERIFIED 后纯脚本重放/漂移检测**无人值守**,
  可挂 monitor/cron;仅漂移、需重新探索时才再叫醒 LLM。符合 JIT:编译后不需前端,
  仅 deopt 才回去。
- 计数与成功率复用 `.stats.jsonl`,状态机只在文件头加 `state` / `shadow_matches`。

---

## 8. CLI 命令面

`lib/cli.py` 新增 `skill` 子命令组(现无):

```
bsession skill record <name>   # 设 BSESSION_RECORD,录到 recorded/<name>.toml
bsession skill run <name>      # 执行器重放一遍,打印 [result] JSON
bsession skill verify <name>   # 测试①②:重放 + 对比 expected_result,过则标 verified
bsession skill test <name>     # 测试④:汇总 .stats.jsonl 最近 N 次成功率
bsession skill monitor <name>  # 读 [monitor] 段,定时重放 + 漂移/变化通知
bsession skill invoke <name>   # 生命周期调度:按 state 自动选 LLM / shadow / 纯脚本
bsession skill list            # 列出已录制脚本及 state / verified / 成功率
```

- 参数注入沿用 `BSESSION_FORM`;`secret=true` 字段只从 env/form 取、不落盘。
- `run`/`verify`/`monitor`/`invoke` 共用同一个 `replay.py`,上层包不同判定/循环。

---

## 9. 与现有系统的关系
- **不动 `run.sh` 路径** —— 继续可用;TOML 是并行的第二条固化方式,更结构化、可测、可监控。
- **录制器挂 `cmd_*` 薄壳**,`BSESSION_RECORD` 未设时零变化,现有测试/skill 不受影响。
- **monitor/notify** 复用 `lib/notify.py`;**Chrome 生命周期**复用 reaper + LRU。

---

## 10. 测试策略
- `primitives.py`:纯函数,单测覆盖 find/extract 正则、变量替换、optional/skip 逻辑。
- `recorder.py`:给定一串原语调用,断言产出的 TOML(值→参数、ref→find、密码留空告警)。
- `replay.py`:给定 TOML + 桩 primitives,断言变量表替换、include 拼接、结构化轨迹、
  failed_step 定位。
- `lifecycle.py`:模拟连续调用,断言 cold→shadow→verified 晋级、K/M 阈值、漂移降级。
- `skilltest.py`:断言①②③④判定与 `.stats.jsonl` 汇总。
- CLI 不回归:沿用现有 `tests/`,验证 `cmd_*` 薄壳行为不变。
