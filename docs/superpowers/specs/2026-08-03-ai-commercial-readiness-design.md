# 4yi-cad 商用化设计:AI→真实模型质量线(2026-08-03)

## 背景与目标

4yi-cad 当前是"可部署 demo"状态:prompt → LLM agent → CadQuery/FreeCAD 脚本 → 执行/修复 → FCStd/STEP/STL + 预览 → FreeCAD GUI bridge 加载。商用化被两类缺口挡住:

1. **AI 质量不可量化**:没有 eval 集、没有成功率度量(`tests/` 里全部 AI 测试走 mock gateway),readiness 端点(`app/main.py` `_production_readiness`)只覆盖基建/安全/license,没有 AI 质量 gate。
2. **发布级 blockers**:`worker_isolation`(GA)、`license_gate`(public_beta+)、`durable_storage`(public_beta+)、共享 GUI 桌面、noVNC 无会话鉴权、healthcheck 误报。

本设计的既定决策(已确认):

- **目标层级**:付费 pilot / private beta 优先,GA blockers 并行推进。
- **旗舰场景**:建筑/社区总图(site layout,FreeCAD 域);机械零件/装配为次级场景。
- **范围**:AI 质量线为主线详细展开,商用 blockers 作为并行 workstream 一并规划。
- **总体策略**:Eval-first 测量驱动(A)为骨干,叠加结构化生成(B)与人审工作流(C)。

## 现状事实基础(勘察结论)

- Agent loop:`app/agent/loop.py` `run_generation()`,有界工具调用修复循环,默认 3 次尝试,双引擎工具 `run_cadquery`/`run_freecad`,失败错误以 tool message 回喂重试。
- 几何校验:CadQuery worker 仅查 `result` 存在 + 非零体积 + 导出成功;FreeCAD worker 有 `isValid`/体积/导出校验;**通用 watertight/manifold/意图符合度校验不存在**。site-layout 域独有质量门(`site_layout_quality_error`、`site_layout_reference_quality_report` 11 项打分、audit+repair pass);机械域无质量门。
- Eval 基建:**不存在**(无语料、无 benchmark、无成功率度量)。
- 人审:有版本历史(`design_versions`)+ rollback + 乐观并发;**无 diff、无 approve/reject 工作流**。
- 持久化:SQLite(`CAD_SESSION_DB_PATH`,有 /tmp fallback)+ 文件系统 artifact store;无 durable job queue,生成是同步 SSE 流。
- Bridge:addon 轮询 + 命令队列;`CAD_BRIDGE_ALLOW_MACRO_EXEC=1` 在 GUI 镜像默认开启(`Dockerfile.freecad-gui:106`),等于对活 GUI 会话的远程任意代码执行面。

## 第一节:Eval 基建与商用基线(Phase 0)

仓库新增 `evals/` 目录:

- **语料**:60–80 个 site-layout 场景,三档分层——T1 简单住宅组团、T2 混合功能社区、T3 复杂总图(滨水/高差/高层组合);另加 20 个机械次级场景。每 case 一个 YAML:`prompt`、`domain`、`tier`、必需要素(role groups、对象数下限、特定构件)、通过阈值。
- **Runner**:`scripts/eval/run_eval.py`,在 x86_64 Docker 内直接调 `run_generation()` 走真实 gateway;每 prompt 3 次重复取方差;记录:成功与否、修复次数、耗时、token 成本、产物路径。内置成本上限与超时保护。切 10-prompt smoke 子集供快速回归;完整跑为手动触发。
- **四层打分**:
  - L1 执行成功(loop 返回 ok)。
  - L2 几何有效:isValid、watertight/manifold(OCC/trimesh)、体积/包围盒 sanity —— 新写的通用校验模块,双引擎共用。
  - L3 场景符合度:复用 `site_layout_reference_quality_report` 11 项打分 + case 声明的必需要素。
  - L4 人工评分:抽样 1–5 分 rubric(几何合理性、意图符合度、可编辑性)。
- **商用验收指标**(pilot 承诺依据):生成成功率 ≥90%、几何有效率 ≥95%、FCStd 可加载 ≥95%、site 质量分达标率 ≥85%、人工评分 ≥4/5。报告输出 JSON + Markdown,历史留存做回归对比。
- **新增 `ai_quality` readiness gate**:readiness 端点读最新 eval 报告;`private_beta` 要求基线已记录,`public_beta`/`ga` 要求阈值达标。

## 第二节:数据驱动的质量加固(Phase 1)

以基线失败分类驱动,不预先猜测:

- **修复循环增强**:回喂内容从纯 traceback 扩展为几何检查结果 + 场景审计结果;按 tier 分配尝试预算(T3 允许 5 次);失败时引导模型先 inspect 再改。
- **通用几何门进执行路径**:L2 校验接入 `default_execute`/`default_freecad_execute`,失败像 site gate 一样触发自动修复;机械域从此有质量门。
- **Best-of-N**:仅对 T3 难档并行生成 2–3 候选,自动打分取最优;成本受控。
- **确定性后处理**:单位归一、recompute、清除 invalid 对象。

## 第三节:旗舰场景结构化生成(Phase 2)

site 类 prompt 改两段式:

1. LLM 第一步输出**结构化 SitePlan JSON**(schema 校验:地块/路网/建筑/水景/配套 + 参数);schema 失败重试是文本级的,远比几何级重试便宜。
2. 确定性渲染器(扩展现有 `app/cad/site_layout_templates.py`)把 SitePlan 渲染为几何。
3. LLM 第二步只在渲染结果之上做定制细节脚本。

自由生成路径保留为 out-of-schema prompt 的 fallback;eval 同时度量两条路径,路由界线以数据定。

## 第四节:人审边界(Phase 3,pilot 必需)

- `design_versions` 增加 `review_status`:`ai_draft → approved / rejected`,含审核人与备注。
- 正式交付格式(STEP、TechDraw PDF)导出按安装配置:要求 approved 才放行,或未审核时加"AI 草案"水印。
- **Diff**:版本对比 = 脚本 diff + scene-tree diff(基于 viewer_scene JSON 的对象增删改)+ 预览图并排。几何布尔 diff 明确不做。
- UI 与 TechDraw 标题栏标注"AI 草案 / 已人工审核";pilot 合同带免责条款(AI 输出非工程交付物,需人工复核)。

## 第五节:并行 workstream(商用 blockers)

- **W1 硬隔离**(对应 `worker_isolation` GA gate):FreeCAD 执行 worker 拆独立 `route:none` 服务;egress block、read-only rootfs、seccomp、tmpfs workspace;`CAD_BRIDGE_ALLOW_MACRO_EXEC=1` 全局开关改为 per-session 签名命令 token + 操作 allowlist;healthcheck 把 GUI/bridge heartbeat 纳入 readiness(修"GUI 崩了仍报 200"误报)。
- **W2 持久化/运维**(对应 `durable_storage` gate):`CAD_DATA_DIR` 落 durable 卷(EBS/PVC),备份/恢复/配额/GC 策略;generation job 落 SQLite,崩溃可见、可恢复。
- **W3 License gate**:第三方许可证清单(FreeCAD LGPL、OCCT LGPL 2.1+exception、noVNC MPL 2.0 组合)、NOTICE 文件、source offer、法务确认后置 `FOURYI_CAD_LICENSE_REVIEW_ACCEPTED`。
- **GUI 多用户**:private beta 阶段用 session-token 强绑定共享桌面过渡;per-user GUI runtime 归入 GA 前提。

## 里程碑与验收

- **M0**:eval 基建 + 真实基线报告。
- **M1**:加固后复测,指标达标 → 开 pilot(private_beta readiness 全绿含 `ai_quality` 基线)。
- **M2**:结构化生成 + 人审工作流上线,pilot 中迭代。
- **M3**:W1–W3 收口 + x86_64 全量 smoke(开 GUI、AI prompt、生成 FCStd、bridge 加载、保存、重连)→ public_beta/GA 判定完全以 readiness 端点绿灯为准。

## 测试策略

- 打分函数、通用几何校验、SitePlan schema 与渲染器:TDD。
- eval runner 本身:mock gateway 单测。
- 真实 eval 跑:仅 x86_64 环境,手动/定时触发,不进默认 CI。

## 明确不做(本计划范围外)

- 几何布尔 diff。
- GPU 渲染 / 多模态输入。
- 把 Best-of-N 用于全部档位(仅 T3)。
- pilot 阶段的 per-user GUI runtime(GA 前提,先 token 绑定过渡)。
