# DDLHub 上线前安全评审报告

> 评审日期：2026-10-30 · 对象：v0.2.0（commit `9107041`）· 方式：白盒代码审计（只读，未改动代码）
> 评审人视角：攻击者思维 + 防御者标准。工具：grep 手工审计、pip-audit 2.10.1（OSV 对照，2026-10-30 时点）。

---

## 1. 结论摘要

**不存在阻断上线的严重问题。** 未发现 RCE、SQL 注入、反序列化、路径穿越、命令注入；SQL 全参数化；无 `|safe` 滥用（Jinja2 autoescape 默认开启）；无 TLS 校验关闭；无硬编码凭证；依赖（pip-audit/OSV 2026-10-30）无已知漏洞。关键结论：

1. **【中·已确认】** 平台侧数据 `source_url` 未做 scheme 校验即渲染为 `<a href>`，存在 `javascript:` URL 的同源 XSS（SEC-01）——一行入库校验可消除。
2. **【中·条件性·已确认】** 本地模式对首个 GET 自动发会话 cookie：在**共享电脑**上，同机其他本地用户打开页面即获得完整会话（SEC-02）。个人笔记本场景影响有限。
3. **【低→中·已确认机制】** 自定义平台的 `entry_url/portal_url` 由用户填写并被服务端主动访问，构成受限 SSRF 探测面（SEC-03），受单用户模型约束。
4. **【低·设计取舍】** LAN 模式明文 HTTP、cookie 无 Secure 标志（http 上浏览器会拒收 Secure）——README 已提示，需更显著（SEC-04）。
5. **【低·供应链】** 无锁文件，构建不可复现；pip-audit 当前干净（SEC-05）。

---

## 2. 攻击面与信任边界

**组件与数据流**

```
浏览器 ──HTTP──► Harden中间件 ──► FastAPI 路由 ──► core(sync/upsert/reminders) ──► SQLite(WAL)
                     │                                   │
                     │                                   ├─► 系统钥匙串（凭据）/ KEK
                     │                                   └─► 适配器 ──HTTPS──► 外部平台
                     └─ APScheduler（内部：cron 同步 / 每分钟提醒 tick）
```

**入口（攻击面）**

| 入口 | 方法 | 认证 | 输入 |
|---|---|---|---|
| `GET /` `/settings` `/login` `/static/*` | GET | 本地=自动发 cookie；LAN=令牌交换 | query 参数（view/account/course/status/days7/token） |
| `POST /api/sync` `/api/accounts` `/api/accounts/{id}/{delete,relogin,interactive-login}` `/api/assignments/{id}/{action,offsets}` `/api/courses/{cid}/toggle` `/api/channels/{key}/toggle` `/api/notify/test` | POST | 同源校验 + 会话 cookie（本地首 GET 后持有） | Form 字段（含平台返回的标题/URL/DDL） |
| 调度器 job（不可直达） | — | — | DB 内数据 |
| 适配器对外 | — | — | 平台响应（半可信：JSON/HTML 经解析进库并渲染） |

**信任边界与关键假设**

1. 浏览器 ↔ 本机服务：以 Host 白名单 + Origin/Sec-Fetch-Site + 会话 cookie 划界（反 CSRF/DNS rebinding 已实现）。
2. **平台数据是半可信的**：教师/平台侧可注入任意标题、URL、DDL——这是本次最重要的边界假设，SEC-01 即源于此。
3. LAN 模式：同一 Wi-Fi 内的设备是潜在攻击者，令牌是唯一门禁。
4. **本机其他本地用户被视为可信**（本地模式自动发 cookie）——这是默认假设，SEC-02 挑战了它在共享主机上的有效性。

---

## 3. 发现清单

| ID | 严重度 | 类别 | 位置 | 问题 | 攻击路径 | 状态 | 修复建议 |
|---|---|---|---|---|---|---|---|
| SEC-01 | **中** | CWE-79 / A03:2021 | `ddlhub/web/templates/_card.html:14`；`ddlhub/core/upsert.py:108,149-150`；`ddlhub/web/static/app.js:20-22` | `source_url` 未校验 scheme 即渲染为 `<a href>` | 平台返回 `source_url="javascript:…"` → 用户点标题 → 同源 JS 执行 | 已确认 | upsert 入库前 scheme 白名单（http/https，否则置 None）；测试锁定 |
| SEC-02 | **中（条件性）** | CWE-306 / A07:2021 | `ddlhub/web/harden.py:86-90` | 本地模式首个 GET 自动发会话 cookie，共享主机上同机其他本地用户即获完整会话（读数据/改状态/删账号） | 实验室/共享电脑：第二用户开 `http://127.0.0.1:8787/` | 已确认（利用依赖部署场景） | 增加 `DDLHUB_LOCAL_TOKEN=1`：本地也强制令牌交换；或首启时在控制台输出一次性配对码 |
| SEC-03 | 低→中（条件） | CWE-918 / A10:2021 | `ddlhub/adapters/custom.py:33-41,68`；`ddlhub/adapters/base.py:189-196`；`ddlhub/core/interactive.py:96` | 自定义平台 `entry_url/portal_url` 用户可控，服务端会主动访问（httpx GET + 浏览器 goto），可探测内网/链路本地地址 | LAN 持令牌者添加 `entry_url=http://169.254.169.254/` 等内网地址，通过 health 返回判定端口/服务存活 | 已确认（机制）；反馈通道有限（不回显内容） | 解析 host 后默认拒绝环回/私网/链路本地段，`DDLHUB_ALLOW_PRIVATE_URL=1` 显式放行 |
| SEC-04 | 低 | CWE-614/319 | `ddlhub/web/harden.py:84-89` | LAN 明文 HTTP；cookie 无 Secure（http 下浏览器拒收 Secure） | 同 Wi-Fi 嗅探会话 cookie | 已确认（设计取舍） | README 显著标注；中期提供 HTTPS（自签/本地 CA）选项 |
| SEC-05 | 低 | CWE-1104 | `pyproject.toml` | 依赖为范围约束、无锁文件，构建不可复现 | 构建期引入被投毒的新版本 | 已确认 | uv/pip-tools 生成 lock 入库；CI 加 pip-audit |
| SEC-06 | 低 | CWE-693 | 全部响应 | 缺 CSP、X-Content-Type-Options、X-Frame-Options、Referrer-Policy | 深度防御缺失；CSP 也能压缩 SEC-01 的影响 | 已确认 | 响应头中间件：`default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data: https:; frame-ancestors 'none'` |
| SEC-07 | 低 | CWE-20 | `ddlhub/adapters/xuexitong.py:59-67` | backclazzdata 的 `id` 字段未做数字校验即拼入导航 URL（参数注入） | 平台异常响应注入 `&`/`#` 片段改变导航参数（origin 固定，影响有限） | 已确认 | `if not class_id.isdigit(): continue` |
| SEC-08 | 信息 | CWE-312/532 | 多处 | ①`mask_username` 对手机号暴露前2后2；②create_account 错误信息经 URL query 重定向（进本机 history）；③日志含课程/作业名；④`.env` 文件权限未收紧 | 本机残留类，无远程路径 | 已确认 | 文档标注；②改为 session flash |
| SEC-09 | 信息 | — | 全局 | 通过项：SQL 参数化、无命令执行/反序列化/路径穿越/TLS 关闭、CSRF 三重防护、keyring 白名单、LAN 令牌门（`9107041` 已修）、无硬编码凭证、git 历史无真实样本（首提交前已脱敏） | — | 已确认 | — |

---

## 4. 重点问题详情

### SEC-01 `source_url` scheme 未校验 → 同源 XSS（中）

**证据**
```html
<!-- ddlhub/web/templates/_card.html:13-14 -->
{% if row.a.source_url %}
<a href="{{ row.a.source_url }}" target="_blank" rel="noopener noreferrer">{{ row.a.title }}</a>
```
```python
# ddlhub/core/upsert.py:108（新增）/149-150（更新）——source_url 原样入库，无 scheme 校验
source_url=d.source_url,
```
Jinja2 autoescape 会转义引号与标签，但**不会**中和 `javascript:` 伪协议——`href="javascript:…"` 合法渲染。

**触发条件**：平台侧数据源返回 `source_url="javascript:fetch('/api/accounts/1/delete',{method:'POST',headers:{'Content-Type':'application/x-www-form-urlencoded'}})"`。谁能量产这种数据：被入侵的教师账号、平台页面被 XSS、或恶意 JSON 响应（学习通接口走 HTTPS，攻击面主要是平台侧内容本身）。用户点击该作业标题即触发。

**影响**：会话 cookie 为 HttpOnly 不可读，但脚本运行在应用同源——可代替用户调用全部 POST API（删账号、改作业状态、改配置），可读取时间线/设置页 DOM（含课程与作业数据）。`rel="noopener noreferrer"` 与 `target=_blank` 不影响 javascript: URL（同页执行）。

**状态**：已确认（代码路径确定；未做真实平台注入，PoC 属本地构造）。

**修复（具体）** —— 在 `core/upsert.py` 增加入库净化，同时给渲染层兜底：
```python
_SAFE_URL_SCHEMES = ("http", "https")

def _safe_external_url(u: str | None) -> str | None:
    if not u:
        return None
    parts = urlsplit(u)
    return u if parts.scheme in _SAFE_URL_SCHEMES and parts.netloc else None
```
Draft 入库处：`source_url=_safe_external_url(d.source_url)`。验证：fixture 加 `javascript:` 样本，断言落库为 None；渲染后 href 不含 `javascript:`。

**复测方法**：新增 `test_upsert_dedupe.py::test_source_url_scheme_allowlist`；手工构造含 `javascript:` 的样本走一遍时间线。

### SEC-02 共享主机上的本地认证缺失（中·条件性）

**证据**
```python
# ddlhub/web/harden.py:86-90
# - 本地模式：首次安全 GET 自动下发（仅 127.0.0.1 可达，等价于本机身份）
if safe and not authed and not path.startswith("/static") \
        and not self.container.settings.lan:
    response.set_cookie(COOKIE_NAME, self.container.session_secret, ...)
```
127.0.0.1 绑定是**每台机器**而非每用户——Windows 快速用户切换下，另一本地用户的浏览器可直达该端口并自动获得会话。

**触发条件**：服务运行中 + 攻击者为本机另一交互用户。个人笔记本上该用户即本人（无意义）；机房/图书馆共用电脑上成立。

**影响**：完整读写（含删除账号、触发同步），凭据本身不可读（在钥匙串与密文中）。

**状态**：已确认（代码确定；严重度取决于部署场景）。

**修复**：加开关 `DDLHUB_LOCAL_TOKEN=1` —— 本地模式复用 LAN 的 `/login` 令牌交换路径（中间件 `not settings.lan` 改为 `not settings.lan or settings.local_token`），首启时把一次性配对码打印到控制台。个人用户不开启则维持现状。**验证**：本地模式开启开关后，无 token 访问应 302 到 /login。

---

## 5. 系统性风险（架构层）

1. **单用户信任模型 vs 共享主机现实**（SEC-02 根因）：所有 API 只有"有/无会话 cookie"两态，没有用户概念。建议长期：把"本机身份"显式化（配对码/token），而非依赖网络位置推定身份。
2. **半可信平台数据缺少统一入站净化层**：目前标题/URL/日期从平台直达 DB 与模板，XSS（SEC-01）只是其中一个出口。建议在 `upsert` 前加统一的 `sanitize_draft()`（URL scheme 白名单、文本长度截断、控制字符清洗），未来每接一个新平台自动受益。
3. **LAN 便利性与安全默认值的张力**（SEC-03/04 根因）：令牌门已正确，但明文 HTTP 与服务端主动外访是固有面。文档要如实，默认值要保守（当前默认 127.0.0.1 ✓）。
4. **供应链可复现性**：无 lock 文件，任何一次重装都可能引入新版本——对个人工具影响低，但与"数据不出本机"的承诺同源，建议一并收紧。

---

## 6. 上线判定

- **个人自有电脑使用：可上线。** 两个中危的利用前提分别是"平台侧投毒"（SEC-01）与"同机其他恶意用户"（SEC-02），个人场景下威胁模型外或影响有限；且 SEC-01 属一行修复。
- **共享/实验室电脑使用：有条件上线。** 条件：① 修复 SEC-01；② 启用 SEC-02 的本地令牌开关。
- 无论何种场景，建议上线前顺手完成路线图"立即修复"档（约 30 分钟工作量）。

---

## 7. 假设与待确认项

**评审所依假设**（信息不足处未停止评审）：
- 单用户模型：设备为个人笔记本；若存在共享场景按 SEC-02 条件性中危对待。
- "大学慕课"按中国大学MOOC（icourse163.org）实现；若实为其他产品需重核登录入口。
- LAN 模式仅在家庭可信网络使用；`data/` 与 `.env` 不随仓库分发（已验证 .gitignore 与 git 历史——真实样本在首次提交 `675429a` 前已脱敏）。

**待确认**：
- 是否存在共享电脑部署场景（决定 SEC-02 定级）。
- 是否有 HTTPS 需求（决定 SEC-04 走文档提示还是实现 TLS）。
- winotify 为小众维护包，供应链信任未深查（已列入 pip-audit 周期检查）。

**未覆盖部分**：GitHub 仓库侧配置（Actions/分支保护，当前未配置 CI）；Windows Credential Manager 自身的访问控制（视为 OS 信任域）；外部平台（学习通等）服务端安全；`data/` 备份介质的加密。

---

## 8. 修复路线图（按 可利用性 × 影响 排序）

**立即修复（上线前，≈30 分钟）**
1. SEC-01：`upsert.py` 加 `_safe_external_url()`（scheme 白名单）+ 回归测试。
2. SEC-02：实现 `DDLHUB_LOCAL_TOKEN=1` 开关 + 文档说明共享主机风险。

**上线前修复**
3. SEC-06：安全响应头中间件（CSP 等 4 个头，≈15 行）。
4. SEC-07：`xuexitong` 课程 id `isdigit()` 校验。
5. SEC-05：`uv lock`/`requirements.lock` 入库 + CI pip-audit 步骤。

**上线后跟进**
6. SEC-03：自定义平台私网地址默认拒绝（可配置放行）。
7. SEC-04：评估 HTTPS 选项；README 嗅探提示加粗。
8. SEC-08：账号删除改 session flash；文档标注日志内容与 `.env` 权限。

---

## 附：本次实际读过的文件

`harden.py`、`routes_api.py`（全文）、`routes_ui.py`（全文）、`main.py`、`config.py`、`__main__.py`、`security.py`、`sessions.py`、`db.py`、`upsert.py`、`reminders.py`、`sync.py`、`interactive.py`、`runtime.py`、`kv.py`、`normalize.py`、`redact.py`、`scheduler.py`、`adapters/base.py`、`xuexitong.py`、`custom.py`、`unipus.py`、`daxuemooc.py`、`zhihuishu.py`、`iwrite.py`、`mock` 相关残留清理点、`notify/*`、`templates/_card.html`/`_statusbar.html`/`settings.html`/`index.html`/`login.html`、`static/app.js`、`migrations/001-003`、`.env.example`、`pyproject.toml`、`.gitignore`、tests 全目录（含 fixtures 与 git 历史抽查）。
