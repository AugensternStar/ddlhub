# DDLHub 技术栈与目录结构报告

> 生成时间：2026-10-30 · 对应版本 v0.2.0（commit `9107041`）
> 定位：本地优先的课程作业 DDL 聚合与提醒工具，仅本机运行，凭据不出本机。

---

## 一、项目规模概览

| 指标 | 数值 |
|---|---|
| Python 源码 | ≈ 5,075 行（core 1,600 / adapters 870 / tests 1,578 / web 路由 695 / 入口+配置 217 / notify 145） |
| 前端（自研部分） | ≈ 540 行（Jinja2 模板 290 + CSS/JS 249）；htmx 与 FullCalendar 为 vendored 第三方 |
| 数据库迁移 | 3 个（001 建库 / 002 课程开关 / 003 账号配置） |
| 测试 | 94 个用例，全绿（pytest） |
| 平台适配器 | 6 个（学习通已全量校准；大学慕课/智慧树/U校园/iWrite 登录已通；自定义平台；测试桩 1 个仅测试进程） |
| 通知渠道 | 桌面 Toast（winotify）+ SMTP 邮件；渠道层可插拔 |
| 运行形态 | 单进程常驻（Uvicorn + APScheduler 线程池），默认仅监听 127.0.0.1:8787 |

---

## 二、技术栈

### 2.1 运行时与后端

| 组件 | 实测版本 | 用途 | 选型理由 |
|---|---|---|---|
| Python | 3.14.6（要求 ≥3.11） | 运行时 | zoneinfo 标准时区库；适配器生态（Playwright/解析库）成熟 |
| FastAPI | 0.162.0 | Web 框架 + 自动 `/docs` | Pydantic 校验、同步路由跑线程池，适配 Playwright 同步 API |
| Uvicorn | 0.54.0 | ASGI 服务器 | 轻量；与 FastAPI 同进程，"一键启动"单进程成立 |
| SQLModel | 0.0.48（SQLAlchemy 2.1.6） | ORM | 类型化模型与 Pydantic 同源；底层仍是 SQLAlchemy |
| APScheduler | 3.11.3（锁定 3.x） | 调度器 | 同进程常驻定时任务；`max_instances/coalesce/misfire_grace_time` 应对休眠唤醒 |
| httpx | 0.28.1 | HTTP 客户端 | 复用会话 cookie 的同步 API；也用于 TestClient |
| tenacity | 9.2.1 | 重试 | 指数退避（1s→2s→4s），4xx 不重试 |
| Jinja2 | 3.1.6 | 模板 | 服务端渲染 + htmx 局部刷新 |
| python-dotenv | 1.2.4 | 配置 | `.env` 约定；红线常量（间隔≥10min）在代码层钳制 |
| python-multipart | — | 表单解析 | FastAPI Form 依赖 |

### 2.2 数据与安全（红线重点）

| 组件 | 实测版本 | 用途 | 选型理由 |
|---|---|---|---|
| SQLite（WAL） | 内置 | 唯一存储 | 单文件、零运维、WAL 支持多线程读写；数据目录可配、网盘路径启动告警 |
| 手写迁移 | — | `schema_version` 表 + `migrations/NNN_*.sql` 顺序执行 | 三条迁移已验证增量机制；不用 Alembic，保持零依赖可读 |
| keyring | 25.7.0 | 密码存 Windows Credential Manager | 启动白名单断言（WinVault/Keychain/SecretService），**明文后端直接拒启** |
| argon2-cffi | 25.1.0 | 主密码派生密钥 | Argon2id(m=64MiB,t=3,p=4)；vault 兜底模式的 KDF |
| cryptography | 50.0.2 | AES-256-GCM | 会话票据加密落库；vault 整包加密；KEK 存钥匙串 |

### 2.3 浏览器自动化与解析

| 组件 | 实测版本 | 用途 | 选型理由 |
|---|---|---|---|
| Playwright | 1.64.1 | 有头人工登录 + 无头作业抓取 | 有头优先系统 **Edge**（channel=msedge，回退 Chromium）；**内存态 context**（无 user_data_dir，磁盘无明文 cookie）；`storage_state()` 加密保存 |
| beautifulsoup4 | 4.15.0 | 作业列表 HTML 解析 | 学习通 `work/list` 页为服务端渲染，解析函数为纯函数可 fixture 回放 |

### 2.4 前端（零构建工具）

| 组件 | 版本 | 用途 | 选型理由 |
|---|---|---|---|
| Jinja2 模板 | — | 页面 + htmx 局部片段 | 无 Node 工具链，pip 装完即用（5 分钟验收） |
| htmx | 1.9.12（vendored） | 局部刷新 | 操作/筛选/开关无需整页刷新、无需手写 fetch |
| FullCalendar | 6.1.15 标准包 + zh-cn 语言包（vendored） | 日历视图 | 本地 vendored 不联网；月/周视图自适应 |
| 原生 CSS/JS | 自研 ≈540 行 | 样式与日历初始化、登录状态轮询 | 响应式（手机可用）；无框架依赖 |

### 2.5 通知与测试

| 组件 | 版本 | 用途 |
|---|---|---|
| winotify | 1.1.0（仅 Windows） | 桌面 Toast；聚焦助手静默失败由"发送测试通知"按钮暴露 |
| smtplib（标准库） | — | SMTP/SSL 邮件；授权码存钥匙串 |
| pytest | 9.1.1 | 94 用例：归一化/幂等/变更检测/提醒引擎/互斥/加固/脱敏/平台登录 |

### 2.6 明确不使用

Docker（与钥匙串/WAL/有头登录三重冲突）、云服务、Celery 等外部队列、前端构建工具链。

---

## 三、目录结构（当前实际状态）

```
ddlhub/
├─ start.bat                     # Windows 一键启动（ASCII+CRLF，失败保持窗口）
├─ start.sh                      # Linux/macOS 辅助脚本
├─ pyproject.toml                # 依赖唯一声明（含 [real-adapters] / [dev] 分组）
├─ .env.example                  # 非敏感配置模板（密码不入 .env）
├─ README.md                     # 快速开始/平台支持表/架构/安全/部署/免责
├─ .gitignore                    # data/ .env .venv 等
│
├─ migrations/                   # 手写 SQL 迁移（schema_version 顺序执行）
│  ├─ 001_init.sql               #   建库：account/session_cache/course/assignment/
│  │                             #   sync_run/notification_log/app_setting + 复合外键
│  ├─ 002_course_enabled.sql     #   course.enabled（课程选择开关）
│  └─ 003_account_config.sql     #   account.config_json（自定义平台配置）
│
├─ docs/
│  ├─ DESIGN.md                  # 设计文档（v2 评审修订版，含 ER 图/接口契约/验收映射）
│  ├─ ADAPTER-GUIDE.md           # 新增平台指南（1 文件 + 1 行注册）
│  └─ STACK-AND-STRUCTURE.md     # 本报告
│
├─ data/                         # 运行时生成（gitignore）：ddlhub.db、logs/
│
├─ ddlhub/                       # ── 应用包 ──
│  ├─ __main__.py                # 入口：加载配置→日志→迁移→发现适配器→容器→调度器→Uvicorn
│  ├─ config.py                  # Settings（.env 解析容错、红线钳制、网盘路径告警）
│  │
│  ├─ core/                      # ── 核心业务（1,600 行）──
│  │  ├─ models.py               # 8 张 SQLModel 表；UTC 约定；复合外键
│  │  ├─ db.py                   # engine/WAL/外键 + 迁移执行器（语句切分器）
│  │  ├─ normalize.py            # 时间归一化（naive UTC 落库/本地展示）+ 档位文案
│  │  ├─ redact.py               # 日志脱敏 Formatter + redact()
│  │  ├─ security.py             # keyring 白名单断言、凭据存取、Vault(Argon2id)、AES-GCM、KEK
│  │  ├─ kv.py                   # app_setting 读写 + 适配器命名空间 KV
│  │  ├─ sessions.py             # SessionStore：storage_state 加密存取（重启恢复登录态）
│  │  ├─ upsert.py               # 幂等 upsert（组合自然键）+ DDL 变更检测（30min 容差）
│  │  ├─ sync.py                 # SyncOrchestrator：互斥锁/限频/退避/失败隔离/三段短事务
│  │  ├─ reminders.py            # 提醒纯函数 + claim-then-send 状态机 + 每分钟 tick
│  │  ├─ interactive.py          # 有头浏览器人工登录管理器（Edge 优先，快照 diff 判定）
│  │  ├─ scheduler.py            # APScheduler：cron 同步 + 提醒 tick + 重试 + 30 天保留
│  │  └─ runtime.py              # Container：settings/db/logger/凭据/渠道/调度器单点持有
│  │
│  ├─ adapters/                  # ── 平台适配器（870 行，1 文件 + 1 行注册）──
│  │  ├─ __init__.py             # discover()：pkgutil 自动 import，坏文件隔离
│  │  ├─ base.py                 # BaseAdapter 契约 + 通用登录判定 + 通用健康检查 + Mixin 预留
│  │  ├─ registry.py             # REGISTRY + @register
│  │  ├─ xuexitong.py            # 学习通：vc3 判定；课程=backclazzdata JSON；作业=浏览器导航+解析
│  │  ├─ daxuemooc.py            # 中国大学MOOC：登录已通，抓取待校准
│  │  ├─ zhihuishu.py            # 智慧树：登录已通，抓取待校准
│  │  ├─ unipus.py               # U校园：登录已通，抓取待校准
│  │  ├─ iwrite.py               # iWrite：登录已通（Unipus SSO），抓取待校准
│  │  └─ custom.py               # 自定义平台：填登录页 URL 即可登录保活（config_json 驱动）
│  │
│  ├─ notify/                    # ── 通知渠道（145 行，可插拔）──
│  │  ├─ base.py                 # NotifierBase + SendResult + send_test()
│  │  ├─ desktop.py              # Windows Toast（winotify）
│  │  ├─ email_smtp.py           # SMTP/SSL 邮件
│  │  └─ __init__.py             # 渠道构建 + 开关存取（app_setting）
│  │
│  └─ web/                       # ── Web 层（695 行 + 模板/静态）──
│     ├─ main.py                 # create_app：装配中间件/路由/模板/静态
│     ├─ harden.py               # 加固中间件：Host 白名单/Origin/Sec-Fetch-Site/会话 cookie
│     ├─ routes_ui.py            # 页面：时间线/列表/日历/设置/LAN 登录（htmx 片段）
│     ├─ routes_api.py           # 操作：作业动作/同步/账号/课程/渠道/档位/测试通知/登录状态
│     ├─ templates/              # base/index/_statusbar/_card/_filters/settings/login/_saved_hint
│     └─ static/                 # app.css/js（自研）+ htmx/FullCalendar/zh-cn（vendored）
│
└─ tests/                        # ── 94 个用例（1,578 行，全离线）──
   ├─ conftest.py                # tmp 库/容器（注入 FakeNotifier 与固定 KEK）/mock 账号
   ├─ mock_adapter.py            # 测试桩适配器（仅测试进程注册）
   ├─ test_normalize.py          # 时区归一化/剩余时间/档位文案
   ├─ test_upsert_dedupe.py      # 幂等/组合键/本地字段不覆盖/倒计时容差
   ├─ test_change_detect.py      # 变更→恰 1 条通知/change_hash 防重发/首次静默
   ├─ test_reminders_pure.py     # 档位计算/范围过滤/到期节点/overdue 护栏
   ├─ test_reminders_engine.py   # tick 集成：补发护栏/完成即停/逾期一次/档位覆盖
   ├─ test_sync_mutex.py         # 互斥/限频（手动豁免）/失败隔离
   ├─ test_harden.py             # Host/Origin/Sec-Fetch-Site/cookie + LAN 令牌交换
   ├─ test_redaction.py          # 日志脱敏/DB 密文扫描（无明文验收）
   ├─ test_course_selection.py   # 课程开关：不抓取/不展示/恢复/空参回归
   ├─ test_xuexitong.py          # 真实样本解析/登录判定/会话加密/有头管理器（本地 HTTP）
   ├─ test_platform_login.py     # 4 新平台注册/通用判定/待校准边界
   ├─ test_custom_platform.py    # 自定义平台：配置推导/创建链路/静默保活
   ├─ test_config.py             # env 容错/红线钳制
   └─ fixtures/xuexitong/        # 真实页面脱敏样本（backclazzdata JSON / 作业列表×3）
```

---

## 四、模块职责与依赖方向

```
web ──► core ──► adapters（经 registry 动态发现）
 │        │
 │        ├──► notify（渠道可插拔）
 │        └──► db/models/security（基础设施）
 └─ harden（安全中间件包裹全部请求）

依赖规则：
- adapters 只依赖 base 契约与 ctx（log/kv/sessions/config），不反向依赖 core 业务
- 数据落库/变更检测/提醒计算全部收敛在 core，适配器只产出 Draft
- web 不直接触达适配器内部，一切经 container（runtime）
```

### 两条关键数据流

**同步链路**（每日 cron 08:00/21:00 / 手动按钮 / 启动补漏；间隔红线 ≥10min，手动豁免）：
`互斥锁 → 建 sync_run → health_check（会话有效？）→ fetch_courses（JSON）→ 逐课程 fetch_assignments（浏览器导航）→ 幂等 upsert → 变更通知（claim）→ 落 run 结果`
单账号失败隔离；每账号一把锁，手动撞 cron 立即返回"正在同步中"。

**提醒链路**（每分钟 tick，无状态）：
`读未完成作业 → 纯函数计算到期节点（T-7d…T-1h，重要+T-12h，单作业可覆盖）→ 补发护栏（只发最近节点）→ claim-then-send（UNIQUE(dedupe_key,channel)）→ 桌面/邮件`
overdue 双护栏；完成/已提交/忽略即停；重启后计划自动恢复。

---

## 五、工程约束备忘

- **红线**：自动抓取间隔 ≥10 分钟（配置钳制）；不绕过验证码（有头浏览器人工登录）；抓取仅限本人账号。
- **事务**：全程短事务、不嵌套（SQLite 单写者）；同步拆三段（建 run → 抓取+upsert → 落结果）。
- **线程**：Playwright 同步 API 只在调度器线程池执行，不进 asyncio 事件循环。
- **编码**：`.bat` 必须 CRLF+ASCII；日志/控制台强制 UTF-8。
- **时间**：库内一律 naive UTC，展示层转本地时区；改时区不迁移历史数据。
