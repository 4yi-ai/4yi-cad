# Plugin V2 — P2 Addon 远程连接(4yi-cad 侧)Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 本地原生 FreeCAD 的 companion addon 能连到云端控制面:ParamGet 配置(ServerUrl/ApiToken)、Bearer 注入、`local-*` 会话自动注册、守卫路径内的 FCStd 回载下载。

**Architecture:** 三块:①服务端把 `local-*` 会话纳入自动注册(复用 `_ensure_shared_remote_freecad_session`)并新增**守卫前缀内**的 FCStd artifact 别名路由(`/api/freecad/sessions/...`,被 P1 bearer 中间件覆盖),bridge 下发的 `fcstd_url` 改指别名;②addon 增加"远程模式":env 无 `CAD_BRIDGE_POLL_URL` 时读 FreeCAD 参数(`User parameter:BaseApp/Preferences/Mod/FourYiCad`)合成 env overlay(全部 bridge URL 由 ServerUrl+local 会话 id 推导),`post_json`/`load_model_bytes` 按 `CAD_API_TOKEN` 注入 `Authorization: Bearer`;③连接设置 Qt 对话框(ServerUrl/ApiToken/测试连接/保存)。

**Tech Stack:** FastAPI、sqlite3(既有 store)、urllib、FreeCAD ParamGet/Qt(PySide);无新依赖。

## Global Constraints

- Spec:`docs/superpowers/specs/2026-08-04-plugin-mode-v2-design.md` §1(认证)§3(同步)。
- **容器/kiosk 模式零变化**:env 有 `CAD_BRIDGE_POLL_URL` → 参数层完全不参与;localhost 豁免不变;既有测试 append-only 全绿。
- `local-*` 会话 id 正则(逐字):`^local-[A-Za-z0-9][A-Za-z0-9_.-]{2,62}$`。不匹配的未知会话 id 仍走现状(404 / None)。
- 守卫别名路由必须在 P1 `GUARDED_PREFIXES`(`/api/freecad/sessions`)之下,**不得**改 `GUARDED_PREFIXES` 本身,**不得**给 `/api/sessions/*`(浏览器 SPA 在用)加 bearer 守卫。
- token 仅存 FreeCAD 参数(用户本机)与请求头;不落日志、不进 Support Bundle。
- addon 参数组(逐字):`User parameter:BaseApp/Preferences/Mod/FourYiCad`,键 `ServerUrl`、`ApiToken`、`LocalSessionId`。
- 测试命令:`cd /Users/yi.zhu/code/4yi-cad && .venv/bin/python -m pytest tests/ -q`(worktree 用主 checkout 的 `.venv`,路径 `/Users/yi.zhu/code/4yi-cad/.venv/bin/python`);提交前全套件 exit 0。
- 分支:worktree `feat/plugin-p2-remote`(从 main 拉),完成后 ff-merge 回 main。

---

### Task 1: 服务端 `local-*` 会话自动注册

**Files:**
- Modify: `app/main.py`(`_ensure_shared_remote_freecad_session`,约 :1343-1384)
- Test: `tests/test_plugin_remote.py`(新文件)

**Interfaces(Produces):** 行为 —— bridge 端点(heartbeat/poll/commands/save,调用点 :2238 等 6 处不动)对 `local-*` id 不再 404,而是像 shared id 一样自动建会话。

**实现(在既有函数内加分支,shared 分支之后):**
```python
_LOCAL_SESSION_ID_RE = re.compile(r"^local-[A-Za-z0-9][A-Za-z0-9_.-]{2,62}$")
```
`_ensure_shared_remote_freecad_session` 中,shared 判定不满足时:若 `_LOCAL_SESSION_ID_RE.match(remote_session_id)` → 走与 shared 相同的 create(镜像 :1356-1383 的写法):`store.create_session(title=f"Local FreeCAD session {remote_session_id}")`;`remote_url=None`;`status="ready"`;`bridge_status="pending"`;metadata `source="local_addon_autocreate"`、`auto_created=True`;事件 metadata `source="local_addon"`。仍不匹配 → 返回 None(现状)。注意:`local-*` 分支**不要求** `_shared_freecad_service_enabled()`(本地插件模式与 GUI backend 配置无关)。

- [ ] **Step 1 失败测试**(`tests/test_plugin_remote.py`;fixture 镜像 `tests/test_api_tokens.py` 的 `_external_client`/store 构造):①外部 client + 有效 token POST `/api/freecad/sessions/local-abc123/bridge/heartbeat`(body 镜像既有 bridge 测试的最小 payload)→ 非 404(会话被建出;GET 会话或再次 heartbeat 200);②id=`local-`(空尾)与 `not-local-x` → 404 现状;③无 token 外部请求 `local-*` → 401(P1 守卫仍生效)。
- [ ] **Step 2 确认失败** → **Step 3 实现** → **Step 4 全套件绿** → **Step 5 提交** `feat(plugin): auto-register local-* remote sessions for native addon`

---

### Task 2: 守卫 FCStd artifact 别名 + bridge fcstd_url 切换

**Files:**
- Modify: `app/main.py`(新路由,放在 `get_session_artifact` :1994 附近;`_queue_freecad_panel_agent_generation` 的 fcstd_url 兜底 :1562-1563)
- Test: `tests/test_plugin_remote.py`(追加)

**Interfaces(Produces,Task 4 addon 消费):**
```
GET /api/freecad/sessions/{remote_session_id}/versions/{version_id}/artifacts/{artifact_name}
  → 由 remote session 解析 workbench_session_id,复用 _get_artifact_store(app).get_artifact(...) 与 FileResponse(镜像 :1994-2011)
  → remote session 不存在或 artifact 不存在 → 404
```
`_queue_freecad_panel_agent_generation` :1562 兜底改为:
```python
f"/api/freecad/sessions/{remote_session.id}/versions/{version.id}/artifacts/fcstd"
```
(`fcstd_ref.get("url")` 优先逻辑不动。kiosk 容器内 addon 走 localhost 豁免,零影响。)

- [ ] **Step 1 失败测试**:①建 workbench session+version+fcstd artifact(镜像既有 artifact 测试的建法)+ remote session 关联 → 外部 client 带 token GET 别名路由 → 200 且 bytes 一致;②无 token → 401(证明在守卫内);③未知 remote_session_id → 404;④队列测试:镜像既有 `_queue_freecad_panel_agent_generation` 相关测试,断言下发命令 payload 的 `fcstd_url` 以 `/api/freecad/sessions/` 开头。
- [ ] **Step 2 确认失败** → **Step 3 实现** → **Step 4 全套件绿** → **Step 5 提交** `feat(plugin): guarded fcstd artifact alias + bridge fcstd_url under bearer guard`

---

### Task 3: Addon 远程配置层 + Bearer 注入

**Files:**
- Modify: `freecad-addon/fouryi_cad_companion/FourYiCadCompanion.py`
- Test: `tests/test_addon_remote_config.py`(新文件;addon 模块无 FreeCAD 也可 import——App/Gui 均有 None guard,直接 `import` 该文件路径或 `importlib` 加载)

**Interfaces(Produces,逐字):**
```python
PARAM_GROUP_PATH = "User parameter:BaseApp/Preferences/Mod/FourYiCad"

def addon_params():                      # FreeCAD.ParamGet(PARAM_GROUP_PATH);App None → None
def local_session_id(params=None) -> str # 读 LocalSessionId;空则 "local-%s" % secrets.token_hex(6) 生成并 SetString 持久化
def remote_overlay_env(base_env=None, params=None) -> dict[str, str]
    # base_env 默认 os.environ。若 base_env 有 CAD_BRIDGE_POLL_URL(非空)→ 原样返回 dict(base_env)(容器模式,参数层不参与)
    # 否则读 params ServerUrl/ApiToken;ServerUrl 为空 → 原样返回(未配置)
    # 非空 → 返回合成 dict:base=ServerUrl.rstrip("/"),sid=local_session_id(params):
    #   CAD_BRIDGE_MODE="workbench", CAD_BRIDGE_AUTOSTART="1", CAD_REMOTE_SESSION_ID=sid,
    #   CAD_BRIDGE_POLL_URL=f"{base}/api/freecad/sessions/{sid}/bridge/poll",
    #   CAD_BRIDGE_HEARTBEAT_URL=f"{base}/api/freecad/sessions/{sid}/bridge/heartbeat",
    #   CAD_BRIDGE_SAVE_URL=f"{base}/api/freecad/sessions/{sid}/save",
    #   CAD_CONTROL_PLANE_URL=base, CAD_API_TOKEN=<ApiToken>(为空则不设),
    #   并保留 base_env 其余键(overlay 覆盖上述键)
def auth_headers(env: dict[str, str]) -> dict[str, str]  # env 有非空 CAD_API_TOKEN → {"Authorization": "Bearer <tok>"};否则 {}
```
接线(全部小改):
- `post_json(url, payload, timeout=10.0, env=None)`:headers 合并 `auth_headers(env or {})`。**所有调用点**传当前 env(bridge 循环里是 `self.env`,panel 处是局部 `env`;逐个 grep `post_json(` 更新)。
- `load_model_bytes` :507 的 `urllib.request.Request` headers 合并 `auth_headers(env)`。
- 启动接线:`:1054-1059` 的 bridge autostart 与 panel autostart 处,把 `os.environ` 换成模块级一次性 `EFFECTIVE_ENV = remote_overlay_env()`(其余 `os.environ` 引用点同样替换为 `EFFECTIVE_ENV`,除 `truthy(os.environ.get("CAD_COMPANION_PANEL_AUTOSTART"))` 这类纯本地开关 —— 由实现者逐点判断:凡参与 URL/会话/token 推导的都走 EFFECTIVE_ENV)。
- 参数对象在测试里用 fake(`GetString/SetString` dict 实现)注入 `params=`。

- [ ] **Step 1 失败测试**:①容器模式:base_env 含 CAD_BRIDGE_POLL_URL → overlay 原样、无 CAD_API_TOKEN 注入;②远程模式:fake params(ServerUrl="https://cad.example.com/", ApiToken="4yi-cad-tok-xyz")→ 三个 bridge URL/CONTROL_PLANE/REMOTE_SESSION_ID 正确、sid 以 `local-` 开头且两次调用稳定(LocalSessionId 持久化);③未配置:两者皆无 → 原样;④`auth_headers`:有/无 token;⑤`post_json` 带 env 注入 header(monkeypatch `urllib.request.urlopen` 捕获 Request,断言 `Authorization`);⑥`load_model_bytes` 下载 GET 同样带 header。
- [ ] **Step 2 确认失败** → **Step 3 实现** → **Step 4 全套件绿** → **Step 5 提交** `feat(addon): ParamGet remote config, env overlay, bearer auth on bridge HTTP`

---

### Task 4: Addon 连接设置对话框

**Files:**
- Modify: `freecad-addon/fouryi_cad_companion/FourYiCadCompanion.py`(对话框 + workbench 命令注册,镜像该文件既有 panel/Qt 写法与 QtWidgets 兼容 import)
- Test: `tests/test_addon_remote_config.py`(追加非 GUI 逻辑测试)

**行为:** 菜单/工具栏命令「4yi: 连接设置…」→ 对话框:`ServerUrl` 文本框、`ApiToken` 密码框(EchoMode Password)、「测试连接」按钮(GET `{ServerUrl}/healthz`,5s 超时,结果标签显示 ✓/错误串)、「保存」写 params 并提示"重启 FreeCAD 生效"。逻辑函数与 GUI 拆开:
```python
def test_connection(server_url: str, timeout: float = 5.0) -> tuple[bool, str]  # urllib GET /healthz;(ok, message)
def save_connection_params(server_url: str, api_token: str, params=None) -> None  # 空 api_token 不覆盖既有值;server_url strip 后 SetString
```
GUI 类仅组装这些函数(Qt 不进单测)。

- [ ] **Step 1 失败测试**:`test_connection` monkeypatch urlopen(200→ok=True;URLError→ok=False 且 message 含原因);`save_connection_params` fake params(写入、空 token 不清既有值)。
- [ ] **Step 2 确认失败** → **Step 3 实现**(含命令注册进 workbench 菜单)→ **Step 4 全套件绿** → **Step 5 提交** `feat(addon): connection settings dialog (ServerUrl/ApiToken, test, save)`

---

## Self-Review

- Spec §1:token 传输 = Authorization Bearer(Task 3);§3:`local-*` 会话(Task 1)、AI 生成回载 HTTP 下载(Task 2 别名 + 既有 `resolve_control_plane_url` 相对路径拼接,CAD_CONTROL_PLANE_URL 由 overlay 提供);版本协商提示 → **降级为 P3 后续**(heartbeat 已上报 addon_version,面板提示不挡 P2 端到端,避免本计划膨胀)。
- 安全:别名路由在 `GUARDED_PREFIXES` 内(外部必须带 token);`/api/sessions/*` 未动;`local-*` 自动注册只能由"已过 bearer 守卫或容器内 localhost"的请求触发(P1 保证),不构成未授权建会话面。
- 零回归:kiosk 路径(env 有 CAD_BRIDGE_POLL_URL)在 overlay 第一行短路;fcstd_url 换别名后 kiosk addon 走 localhost 豁免不受影响。
- xclaw 路由器侧的 SSO 放行(本计划的前置依赖之一)在独立计划 `xclaw docs/superpowers/plans/2026-08-05-app-bearer-passthrough.md`;两计划无代码耦合,联调=端到端手测清单(spec §4)。
