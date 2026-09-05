# MCP 集成指南（双向）

AgentForge 同时是 MCP 生态的**消费者**与**提供者**：

```
┌────────────────────────┐         ┌──────────────────────────┐
│  AgentForge as CLIENT  │         │ AgentForge as SERVER     │
│  (agentforge serve)    │         │ (agentforge mcp serve)   │
│                        │         │                          │
│  config.yaml           │ stdio   │ 暴露给 Claude Desktop /  │
│  ~/.agentforge/mcp.json│ JSON-RPC│ Cursor / 任何 MCP 客户端: │
│  ./.mcp.json           │◄───────►│ sensor_analysis          │
│  ⊂ 多源合并            │         │ rag_search               │
│  ▼                     │         │ create_work_order        │
│  工具注册表             │         └──────────────────────────┘
└────────────────────────┘
```

## 1. 作为 Client：挂载外部 MCP 工具

### 配置三源（后者覆盖前者，按 server 名去重）

| 来源 | 路径 | 适用 |
|---|---|---|
| YAML | `config.yaml` 的 `mcp_servers:` | 部署固定依赖 |
| 用户级 | `~/.agentforge/mcp.json` | 个人跨项目工具箱 |
| 项目级 | `./.mcp.json` | 项目专属（**Claude Code 兼容格式**） |

`.mcp.json` 格式（与 Claude Desktop / Claude Code 一致）：

```json
{
  "mcpServers": {
    "fetch": { "command": "uvx", "args": ["mcp-server-fetch"] },
    "git":   { "command": "uvx", "args": ["mcp-server-git"], "env": {"GIT": "1"} }
  }
}
```

### CLI 管理

```bash
agentforge mcp list                    # 合并视图（标注来源 yaml/file）
agentforge mcp add fetch uvx mcp-server-fetch            # 写入用户级
agentforge mcp add db postgres-mcp --scope project       # 写入项目级 .mcp.json
agentforge mcp remove fetch --scope user
agentforge mcp test fetch            # 真实拉起子进程：握手 + tools/list
```

`mcp test` 是最接近真实运行的验证——它会启动子进程、完成 MCP 握手、
枚举工具再退出。挂载失败的服务器只降级该工具，平台主流程不受影响。

## 2. 作为 Server：ForgeOps 即 MCP 服务

```bash
agentforge mcp serve
```

stdio JSON-RPC 常驻进程，暴露三个垂类工具：

| 工具 | 说明 |
|---|---|
| `sensor_analysis` | 振动频谱/RMS 分析（numpy FFT，ISO 10816 判定） |
| `rag_search` | 设备手册/案例库混合检索 |
| `create_work_order` | Schema 校验的维修工单（MCP 模式下自动批准，无人工在场） |

### Claude Desktop 集成

`claude_desktop_config.json`：

```json
{
  "mcpServers": {
    "forgeops": {
      "command": "/path/to/agentforge/.venv/bin/agentforge",
      "args": ["mcp", "serve"]
    }
  }
}
```

之后在 Claude Desktop 里即可直接问：“分析一下 AC-017 的振动数据”——
Claude 调用我们的 `sensor_analysis`，返回 FFT 峰值与 ISO 判定。

### 安全边界

`python_repl` 与 `dispatch_subagent` **刻意不对外暴露**：远程客户端在宿主机
执行代码/拉起子代理会把攻击面放大到沙箱威胁模型之外。暴露清单在
`mcp_server.py` 的 `EXPOSED_TOOLS` 白名单中，默认最小权限。

## 3. 终端聊天（pi 风格 REPL）

```bash
agentforge chat                       # 新会话
agentforge chat --session <id>        # 续聊
agentforge chat --orchestrator plan_execute
```

REPL 内直接体验全部能力：流式输出、工具执行计时、计划进度、审批决策：

```
you> 诊断 AC-017 振动报警
📋 计划：知识检索 → 并行诊断 → 诊断结论
▶ s1 知识检索
  🛠 rag_search {"query": "…"}
  ↳ ok (212ms) …
▶ s2 并行诊断
  🛠 dispatch_subagent …   (并行专家子代理)
▶ s3 诊断结论
⚠ HITL 创建 P2 级维修工单需要人工批准
批准执行? [y/N] y
```

斜杠命令：`/help /new /sessions /session /trace /tools /model /orchestrator /auto /export /quit`
