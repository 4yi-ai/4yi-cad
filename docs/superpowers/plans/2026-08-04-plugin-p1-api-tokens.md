# Plugin V2 — P1 API Token 认证 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Per-install API token:签发/列出/吊销端点 + Bearer 校验中间件(localhost 豁免),使本地 FreeCAD addon 可安全连入云端控制面;云端 kiosk 模式行为零变化。

**Architecture:** 扩展 `SqliteSessionStore`(同一 SQLite)新增 `api_tokens` 表与四个方法;`create_app` 增加一个 http middleware:对 `/api/freecad/sessions/*` 与 `/api/generate` 的**非本机**请求要求 `Authorization: Bearer 4yi-cad-tok-*`;token 管理端点(`/api/tokens*`)不在守卫路径内(靠平台 SSO 边界保护)。明文 token 仅在创建响应中出现一次,库中只存 sha256。

**Tech Stack:** FastAPI middleware、sqlite3、hashlib/secrets;无新依赖。

## Global Constraints

- Spec:`docs/superpowers/specs/2026-08-04-plugin-mode-v2-design.md` §1(认证)。token 格式 `4yi-cad-tok-<48 hex>`(`secrets.token_hex(24)`);表字段 `id, token_hash, label, created_at, last_used_at, revoked_at`。
- **云端模式零变化**:客户端 host ∈ {`127.0.0.1`, `::1`, `testclient`} 或 `request.client is None` → 免验(容器内 bridge 走 localhost;Starlette TestClient 的 host 恒为字符串 `testclient`,真实 uvicorn 客户端恒为 IP,不会伪造)。现有全部测试必须原样绿(append-only)。
- 守卫路径前缀:`/api/freecad/sessions`、`/api/generate`。未带/无效/已吊销 token → 401,`detail` 分别为 `api_token_required` / `api_token_invalid`。有效 → 放行并更新 `last_used_at`。
- 管理端点:`POST /api/tokens`(body `{label?}` → 201 `{id, token, label, created_at}`,`token` 仅此一次)、`GET /api/tokens`(列表,含 `last_used_at/revoked_at`,**不含 hash/明文**)、`DELETE /api/tokens/{id}`(吊销=置 `revoked_at`,204;不存在 404)。
- 测试命令:`cd /Users/yi.zhu/code/4yi-cad && .venv/bin/python -m pytest tests/test_api_tokens.py tests/test_session_store.py tests/test_main.py -q`;提交前全套件 `-q tests/` 必须 exit 0(基线绿)。
- 分支:worktree `feat/plugin-p1-tokens`(从 main 拉),完成后 ff-merge 回 main。

---

### Task 1: Token store(SqliteSessionStore 扩展)

**Files:**
- Modify: `app/session_store.py`(`_init_db` 新表 + 四方法;镜像该文件既有表/方法的写法与命名风格)
- Test: `tests/test_api_tokens.py`(新文件)

**Interfaces(Task 2 消费,签名逐字):**
```python
def create_api_token(self, label: str | None = None) -> dict  # {"id","token","label","created_at"};token=明文,仅此返回
def list_api_tokens(self) -> list[dict]                        # 各项含 id,label,created_at,last_used_at,revoked_at;无 hash/明文
def revoke_api_token(self, token_id: str) -> bool              # 置 revoked_at;不存在→False;已吊销→True(幂等)
def verify_api_token(self, token: str) -> bool                 # sha256 比对;有效(未吊销)→更新 last_used_at 并 True
```
`SessionStore` 抽象基类同步加这四个抽象方法(该文件既有模式);id 用该文件现有 id 生成方式(uuid);时间戳用文件现有 `utc_now()`。

- [ ] **Step 1 失败测试**(`tests/test_api_tokens.py`,用 tmp_path 建 SqliteSessionStore,镜像 `tests/test_session_store.py` 的构造方式):create 返回 `4yi-cad-tok-` 前缀且长度=12+48;verify(明文)→True 且 last_used_at 落库;verify(错串/空/无前缀)→False;list 不含明文与 hash 字段;revoke→verify False、再次 revoke→True、revoke 不存在→False;两次 create 的 token 不同。
- [ ] **Step 2 确认失败** → **Step 3 实现**(sha256 hexdigest 存 `token_hash`;verify 用常量时间比较 `hmac.compare_digest`)→ **Step 4 全绿**(含既有 `test_session_store.py`)→ **Step 5 提交** `feat(auth): api token store (issue/list/revoke/verify)`

---

### Task 2: 中间件 + 管理端点

**Files:**
- Modify: `app/main.py`(`create_app` 内:middleware + 三个端点;镜像该文件端点风格)
- Test: `tests/test_api_tokens.py`(追加 API/中间件用例;必要时新 fixture 仿 `tests/test_main.py` 的 TestClient 构造)

**Interfaces:** 消费 Task 1 四方法(经 `app.state.session_store` 或该文件现有的 `_get_session_store(app)` helper —— 用现有取法)。

**中间件行为(逐字):**
```python
GUARDED_PREFIXES = ("/api/freecad/sessions", "/api/generate")
豁免:request.client is None or request.client.host in {"127.0.0.1", "::1", "testclient"}
命中守卫且非豁免:
  无 Authorization/非 Bearer/前缀不符 → 401 {"detail": "api_token_required"}
  verify_api_token False → 401 {"detail": "api_token_invalid"}
  True → 放行
```
注意:middleware 里拿 store 用与端点一致的 lazy 取法;store 为 None(未配置)时对非豁免请求返回 401 `api_token_required`(fail-closed)。

**中间件测试要点**:TestClient 默认 host=`testclient` → 现有行为全免验(既有测试自证);要测"外部客户端"路径,用 `TestClient(app, client=("203.0.113.9", 12345))`(httpx/starlette 支持 client 参数;若该版本不支持,改用 `app.middleware` 单元级直测:构造 `Request` scope 伪造 client —— 二选一,报告里说明用了哪种)。用例:外部无 token 打 `/api/generate` → 401 required;带错 token → 401 invalid;带有效 token(先 POST /api/tokens 创建)→ 不再 401(可为其它 4xx/2xx,断言 status != 401);外部打非守卫路径(如 `/healthz`)→ 200 免验;POST/GET/DELETE `/api/tokens` 全流程 + DELETE 404。

- [ ] Step 1 失败测试 → Step 2 确认 → Step 3 实现 → Step 4 目标文件 + `tests/test_main.py` + 全套件绿 → Step 5 提交 `feat(auth): bearer middleware for bridge/generate + token management endpoints`

---

## Self-Review

- Spec §1 覆盖:签发(POST,一次性明文)、吊销、校验、localhost 豁免、fail-closed;`/workbench` 连接 UI 属 P3 不在本计划。macro-exec 的 token 语义随本中间件对 `/api/freecad/sessions/*`(含 commands/run_macro 队列)整体生效。
- 零回归:豁免集合覆盖 TestClient 与容器内 localhost;现有测试不改。
- 类型一致:四方法签名与 Task 2 调用一致;401 detail 两个常量字符串一致。
