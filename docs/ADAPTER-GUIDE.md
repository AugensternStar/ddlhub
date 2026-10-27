# 新增平台适配器指南（ADAPTER-GUIDE）

> 以阶段 3 的 `adapters/xuexitong.py` 为完整范例。核心承诺：**新增一个平台 = 1 个文件 + 1 行注册，核心代码零改动。**

## 1. 文件骨架

```python
# ddlhub/adapters/<key>.py
from .registry import register

@register("<key>")                 # ← 唯一的"注册行"
class <Key>Adapter(BaseAdapter):
    name = "平台显示名"
    capabilities = Capability.EMBEDDED_JSON | Capability.PLAYWRIGHT | Capability.INTERACTIVE
    supports_2fa = True
    login_mode = "interactive"     # credentials=程序化账密登录 | interactive=有头人工登录
    login_entry_url = "https://…/login"   # interactive 模式必填

    def login(self, cred) -> LoginResult: ...
    def fetch_courses(self) -> list[CourseDraft]: ...
    def fetch_assignments(self, course: CourseDraft) -> list[AssignmentDraft]: ...
    def health_check(self) -> Health: ...          # 用已存会话发轻量请求
    def login_success(self, context, page) -> bool: ...  # interactive 模式：登录完成判定
```

文件保存后**无需改任何其他代码**：`adapters/__init__.py` 的 `discover()` 启动时自动 import，
设置页"添加平台"下拉自动出现该平台。

## 2. 选择登录模式（红线相关）

| 模式 | 适用 | 说明 |
|---|---|---|
| `interactive` | 登录有验证码/滑块/扫码的平台（学习通等） | core 弹**有头浏览器**（优先系统 Edge），用户人工登录一次；适配器实现 `login_success()` 判定（如检测票据 cookie `vc3`）；成功后 `storage_state()` **AES-GCM 加密落库**，长期复用。**绝不绕过验证。** |
| `credentials` | 账密直登无验证码的平台 | 实现 `login(cred)`，用 `ctx.sessions` 保存会话 |

会话读写一律走 `ctx.sessions`（`SessionStore`），**适配器不接触明文存储**；有头登录
必须用内存态 context（core 已处理，勿用 `user_data_dir`——Chromium 会明文落盘 cookie）。

## 3. 抓取策略优先级（设计 §5.2）

1. **官方 API / 内嵌 JSON**（如学习通 `mooc1-api/mycourse/backclazzdata`）
2. **服务端渲染页解析**（如 `mooc2-ans/work/listAll`，BeautifulSoup）
3. **Playwright 渲染兜底**（前两级失效时，阶段 3 范例暂未接入，接口已留）

用 httpx 复用会话 cookie：`self.ctx.sessions.load()` → `httpx.Cookies`（见 xuexitong `_http()`）。
请求间随机停顿 1–2s（红线：自动抓取间隔 ≥10 分钟，由 core 调度钳制）。

## 4. 数据契约（core 落库规则，适配器只产 Draft）

- `AssignmentDraft.source_id`：平台侧作业 id（允许仅课程内唯一）；core 统一组合成
  `f"{course_source_id}::{source_id}"` 作自然键 → 幂等 upsert。
- `due_at` **必须带时区**（naive 按本地时区解释）；`None`=无截止（展示但不提醒）。
- `status`: `open | submitted | closed`；`priority`: 1=重要（限时/测验类）。
- 本地字段（完成/忽略/置顶）由 core 管，同步永不覆盖。

## 5. 测试（离线，不联网不登录）

1. 把真实页面/接口样本存到 `tests/fixtures/<key>/`；
2. **解析函数必须是纯函数**（输入 str/dict → 输出 Draft 列表），对 fixture 断言；
3. 登录判定/健康检查用假 context/假 client 打桩；
4. 有头登录管理器可用 `headless=True` + `login_success()` 恒真的一次性假适配器全链路测试
   （见 `tests/test_xuexitong.py::test_interactive_manager_success`）。

平台改版时：表现为 `sync_run=failed` 或解析为空 → 重录 fixture → 只改解析函数。

## 6. 删除平台账号

设置页「删除」按钮：级联清课程/作业/会话/通知记录 + keyring 凭据条目（按 account.uuid）。
