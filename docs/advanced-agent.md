# 前沿 Agent 技术吸收地图（炫技方案）

> 本文档回答一个问题：**2025-2026 前沿 Agent 系统的核心技术，各自在我们这个垂类业务（工业设备运维）里落在了哪一行代码上。**
> 不是概念堆砌 —— 每一项都有实现、有测试、有评测断言。

## 总览：从"单循环"到"多智能体组织"

```
                        ┌─────────────────────────────────────┐
                        │  Planner（任务规划器）               │
                        └──────────────┬──────────────────────┘
                                       │ 显式计划
                 ┌─────────────────────┼──────────────────────┐
                 ▼                     ▼                      ▼
        ┌── Executor step 1 ──┐ ┌── Executor step 2 ──┐ ┌── step 3 ──┐
        │ (ReAct 循环)        │ │  🆚 dispatch_subagent│ │ (ReAct)    │
        │                     │ │  ┌────────┐┌────────┐│ │            │
        │ 🆚 并行工具调用      │ │  │知识研究员││数据分析师││ │ 🆚 HITL   │
        │ asyncio.gather      │ │  │(RAG)   ││(FFT)   ││ │ 审批门     │
        └──────────┬──────────┘ │  └────────┘└────────┘│ └─────┬──────┘
                   │            │  独立上下文·受限工具集 │       │
                   ▼            └──────────┬───────────┘       ▼
        ┌──────────────────────────────────▼───────────────────────┐
        │  Synthesizer（汇总） → 🆚 Critic（Reflexion 审核可打回）   │
        └──────────────────────────┬───────────────────────────────┘
                                   ▼
                  结构化工单（Schema 校验）→ 🆚 长期记忆沉淀
                                   ⇄ 🆚 下次会话自动召回注入
```

🆚 = 本项目实现的先进技术。逐项对照：

## 1. 子代理编排（Claude Code 式 multi-agent）

**前沿原型**：Claude Code 的 subagent 机制 / OpenAI Swarm —— 主代理把独立子任务派给拥有**隔离上下文 + 受限工具集**的专家代理，避免上下文互相污染。

**本项目的形态**：`dispatch_subagent` 工具（[`tools/subagent.py`](../src/agentforge/tools/subagent.py)）。诊断任务的"并行诊断"步骤中，主代理一轮并行派出：
- **知识研究员**（只配 `rag_search`）：检索手册判据与历史案例
- **数据分析师**（只配 `python_repl` + `sensor_analysis`）：做 FFT 频谱分析

**工程要点**：
- 子代理跑**临时内存循环**，只有主代理持久化转录 —— 子代理试错不污染审计主线
- **防递归**：子代理白名单天然不含 `dispatch_subagent`，专家不能再拉专家
- 子代理的工具调用仍走审计过的 `ToolRegistry.run`，session 级审计完整
- `asyncio.gather` 并行执行，整体超时兜底

## 2. 并行工具调用（OpenAI / Anthropic 原生能力）

**前沿原型**：模型单轮返回多个 tool_calls（前端 agent 用它扇出独立工作）。

**本项目的形态**：ReAct 内循环（[`agent/core.py`](../src/agentforge/agent/core.py) `_react`）用 `asyncio.wait(FIRST_COMPLETED)` 驱动任务组：多工具并发执行，每个调用有独立事件桥，**执行期间持续把事件流给客户端**（不是跑完才吐）。

**为什么难**：流式 + 并行 + 事件顺序不能乱。tool_start 全部先发（用户立刻看到派了哪些活），tool_end 按结果就绪顺序补齐，持久化顺序与 tool_call_id 对应。

## 3. Human-in-the-Loop 审批门（Claude Code 权限确认模式）

**前沿原型**：Claude Code 执行敏感操作前请求用户确认；企业 Agent 平台的 approval workflow。

**本项目的形态**：P1/P2 级维修工单会**中断真实生产**，因此创建前挂起：
1. `create_work_order` 工具落一条 `Approval`（pending），通过事件桥实时推送 `approval_required` 到 UI
2. 工具**在 SSE 流内等待**人工决定（轮询 DB），UI 弹出审批卡片：批准 / 拒绝
3. `POST /api/forgeops/approvals/{id}/decide` 写入决定 → 工具恢复 → 批准则建单+沉淀记忆，拒绝则把"用户拒绝了"作为结果回传给模型自行调整

**工程要点**：`auto_approve` 三层覆盖（全局配置 < 每请求 < 默认），CI/评测全自动，演示时一键切 HITL；审批等待超时有明确降级路径。

## 4. 自我批判 / Reflexion

**前沿原型**：Reflexion / Self-Refine —— 生成后由 critic 审查，不通过则带具体意见重试一轮。

**本项目的形态**：Synthesizer 输出后，**Critic**（质量审核员）对照步骤证据审查最终回答（数值一致性/遗漏警告/是否切题），输出 JSON 裁决；不通过则带着 issues 强制修订**一轮**（有界：无界自我循环烧 token 收益趋零）。审核轨迹（`critic_verdict` 事件 + `critic_revised` 标记）进入 Trace。

## 5. 长期记忆（MemGPT / Letta 式）

**前沿原型**：MemGPT/Letta 的跨会话语义记忆；企业 Agent 的"越用越懂你"。

**本项目的形态**：每次生成工单，自动沉淀一条设备维度诊断记忆（`memories` 表：设备 ID / 类型 / 内容 / 会话溯源）。下次任务提及同一设备时，相关记忆**自动注入规划上下文**（`memory_recalled` 事件可见）。

**诚实边界**：当前是结构化键值召回（设备 ID 精确匹配）。向量语义召回是明确的下一步，检索接口已按可替换设计。

## 6. 已在第一期落地的技术（回顾）

| 技术 | 来源 | 实现 |
|---|---|---|
| Plan-and-Execute | Manus / BabyAGI 系 | `agent/core.py` 规划-执行-汇总 |
| MCP 协议 | Anthropic MCP | `mcp_client.py` + 示例 Server |
| 混合检索 RRF | 生产 RAG 共识 | `rag/` BM25+向量+RRF |
| 结构化输出 Guardrail | OpenAI Structured Outputs | Schema 校验工单 |
| 全链路可观测 | LangSmith/Langfuse | 审计日志重建 Trace |
| 评测回归门禁 | DeepEval/promptfoo | `eval/` + CI gate |
| 沙箱代码执行 | OpenAI Code Interpreter | RLIMIT 子进程 + 诚实威胁模型 |
| 多厂商故障转移 | LiteLLM/网关共识 | 首事件前 failover，绝不重复输出 |

## 评测如何保护这些技术不被改坏

```yaml
- name: diagnosis_plan_execute_full_chain
  expect_tools: [rag_search, dispatch_subagent, create_work_order]  # 多智能体扇出
  expect_contains: ["轴承外圈磨损", "0.87"]                          # 结论内容
```

每项技术在评测集里都有对应断言：子代理派发看工具序列、HITL 看审批落库、
记忆看跨会话召回接口。改坏任何一环，CI 红灯。

## 下一步（Roadmap）

- **Deep Research 模式**：迭代式"检索→反思缺口→再检索→引用合成"，生成全厂设备健康周报
- **A2A 协议**：跨团队 Agent 互调（运维 Agent ↔ 备件库存 Agent）
- **向量语义记忆**：记忆召回从设备 ID 精确匹配升级为语义检索
- **Agent-as-MCP-Server**：把 ForgeOps 诊断能力反向暴露给 Claude Desktop / Cursor
