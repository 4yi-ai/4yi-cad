# 4yi-cad Plugin 模式 V2:本地原生 FreeCAD + 云端控制面(2026-08-04)

## 决策背景(用户拍板)

云端 GUI 流(noVNC kiosk)可以走通但天花板低:像素级运维面大、每用户一个 4GB GUI 容器的成本模型差、体验上限是远程桌面。**产品主路径改为 Plugin 模式**:用户在本地原生 FreeCAD 里安装 4yi companion addon,连接云端控制面获得同一套 AI 能力;云端 kiosk 降级为零安装的演示/试用入口(修到能用即冻结,不追求像素完美)。

对商用化 spec 的影响:GA blockers 中"per-user GUI 隔离"在 Plugin 模式下**天然消失**(GUI 在用户本机);W1 只剩无 GUI 的执行沙箱隔离,范围缩小。

## 现状资产(复用 ~90%)

- `freecad-addon/fouryi_cad_companion/` 已是标准 FreeCAD workbench:面板 UI(Prompt/Explain/Patch/Bundle)、bridge 轮询循环、命令执行,全部可直接在本地 FreeCAD 运行。
- 云端控制面 API 已存在:`/api/freecad/sessions/{id}/bridge/{heartbeat,poll,commands,save}`、`/api/sessions/{id}/versions`(版本库/rollback)、`/api/generate`(agent loop)。
- 缺的只有三件事:**认证、分发、同步**。

## 1. 认证:per-install API token

现状:bridge 端点无自身鉴权,靠"同容器 localhost + 平台 SSO 边界"保护。本地 addon 从公网连入,必须补 token:

- **签发**:app 的 Web 工作台(`/workbench`)新增「连接本地 FreeCAD」页:生成 `4yi-cad-tok-<random>`,展示一次,存储仅哈希(SQLite 新表 `api_tokens`:id、token_hash、label、created_at、last_used_at、revoked_at)。同页可列出/吊销。
- **校验**:FastAPI 中间件对 `/api/freecad/sessions/*` 与 `/api/generate` 接受 `Authorization: Bearer 4yi-cad-tok-*`;无 token 时保留现有行为(容器内 localhost 调用不带 token —— 以 client host 判定,127.0.0.1 免验,保证云端 kiosk 模式零改动)。
- **传输**:仅 HTTPS(平台域名天然满足)。
- **与 GA 安全项合并**:该 token 机制同时替代 `CAD_BRIDGE_ALLOW_MACRO_EXEC=1` 的全局开关语义 —— 远程 `run_macro`/`load_model` 命令必须携带有效 token,实现商用化 spec W1 中"bridge macro-exec 改 per-session token"。

## 2. 分发:addon 发布与安装

**前提:独立 addon 仓库** `github.com/4yi-ai/fouryi-cad-addon` —— FreeCAD Addon Manager 要求 addon 位于 git 仓库根目录:

- 内容 = 主仓 `freecad-addon/fouryi_cad_companion/`(workbench + 图标 + `package.xml`);
- 主仓 GitHub Action 用 `git subtree split` 自动同步,单一真源仍在主仓;
- **版本纪律**:`package.xml` version = git tag = Support Bundle 的 `addon_version`,三处一致;`<freecadmin>1.0</freecadmin>` 声明最低 FreeCAD 版本。

**三条安装通道(按推荐顺序)**:

1. **自定义仓库(主推)**:用户在 Addon Manager → Custom repositories 添加 `https://github.com/4yi-ai/fouryi-cad-addon`,搜索安装;Addon Manager 按 `package.xml` 版本做**自动更新提示**。`/workbench`「连接本地 FreeCAD」页提供可复制 URL + 图文三步。
2. **zip 兜底**:连接页直接下载 zip,解压到 Mod 目录(Linux `~/.local/share/FreeCAD/Mod`、Windows `%APPDATA%\FreeCAD\Mod`、macOS `~/Library/Application Support/FreeCAD/Mod`)重启;无自动更新,适用内网/无 git 环境。
3. **官方 addon 索引(后期,不挡商用)**:向 `FreeCAD/FreeCAD-addons` 提 PR,社区审核合入后所有 FreeCAD 用户默认可搜到;要求开源许可证明确、无捆绑二进制;addon 连接商业云服务允许(有先例),需在描述中写明。

**版本协商**:addon heartbeat 已上报 `addon_version`,控制面对过旧版本在面板提示升级(P2 顺手实现)。

**配置**:addon 新增「连接设置」对话框:填 App URL(`https://<install-host>`)+ token,写入 FreeCAD 参数(`User parameter:BaseApp/Preferences/Mod/FourYiCad`:`ServerUrl`、`ApiToken`),替代现在的 `CAD_BRIDGE_*` 环境变量来源(env 仍优先,容器模式不变)。

## 3. 同步:本地文档 ↔ 云端版本库

会话模型:本地 addon 以 `local-<机器指纹>` 为 session id(替代 `shared-freecad-gui`),向控制面注册;一个 install 可多个本地会话并存。

- **Pull**:面板「打开云端版本」→ 列出 sessions/versions → 下载 FCStd(现有 artifact API)→ 本地打开。
- **Push**:面板「保存到云端」→ 上传当前 FCStd 为新 version(现有 `save`/versions API,base_version 乐观并发已存在,冲突时提示拉取最新)。
- **AI 生成回载**:`Send Prompt` → 控制面 agent 生成 → 新 version → addon 收到 `load_model` 命令 → 下载 FCStd → 本地打开/刷新。与云端模式同一命令协议,仅传输从"容器内路径"变为"HTTP 下载"(bridge 命令的 payload 增加 artifact 下载 URL 字段,云端模式沿用本地路径字段,向后兼容)。
- **明确不做(V2)**:实时协同、双向增量 diff 同步、离线队列。冲突模型 = 乐观并发 + 手动解决。

## 4. 兼容与测试

- 支持 FreeCAD 1.0+(addon 已在 1.0.0 验证);Windows/macOS/Linux(addon 纯 Python + Qt,无平台代码;机器指纹用跨平台实现)。
- 测试:token 中间件与 localhost 豁免(pytest);addon 侧连接/同步逻辑单测(mock 控制面);端到端手测清单:本地 FreeCAD → 配 token → Send Prompt → 模型回载 → push 版本 → Web 工作台可见。

## 交付切分(各自独立可用)

- **P1 token 认证**(控制面):签发/吊销 UI + 中间件 + localhost 豁免。云端模式零影响。
- **P2 addon 连接设置 + 远程 bridge**:参数化 ServerUrl/ApiToken、HTTP 下载回载、`local-*` 会话注册。
- **P3 分发**:拆 `fouryi-cad-addon` 仓库 + subtree 同步 Action + package.xml 版本化(tag/`freecadmin`)+ `/workbench` 连接页(自定义仓库 URL + zip 下载 + 图文);官方 FreeCAD-addons 索引 PR 放 P3 之后。
- 预估合计 1–2 周;P1 可先行(它同时是 GA 安全项)。

## 明确不做

- 桌面端打包版 FreeCAD(自带 addon 的定制安装包)—— 后续再评估。
- 本地 LLM/本地执行 —— LLM 与 agent loop 始终在云端(计费与 gateway 契约不变)。
- 云端 kiosk 的进一步打磨(冻结在"能用"水平)。
