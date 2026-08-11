# Personal-Agent 设计文档 v0.2

## 1. 项目定位

个人工作辅助 Agent。核心目标:

> 将用户日常工作中的任务输入,通过 LLM 理解、规划、执行,并将过程和结果沉淀到 Obsidian 知识库。

覆盖场景:命令行操作、IDE 代码开发、Obsidian 笔记、浏览器搜索、以及"导师微信消息"这类外部任务来源的手动接入。

微信/QQ 不做账号自动化操作或消息自动读取 —— 这类方案账号封禁风险高,且非官方协议不稳定。第一版采用"手动投喂"(用户复制粘贴到 Inbox.md),后续如有必要再评估更安全的读取方式。

---

## 2. 相较于初版规划的改动与理由

| 改动点 | 初版规划 | 本版设计 | 理由 |
|---|---|---|---|
| Tool 统一接口 | Phase 6 才定义 | Phase 0 就定义 `tools/base.py` | 避免早期工具代码到 Phase 6 全部推倒重写 |
| 危险命令拦截 | 硬编码在 `executor.py` | 独立配置 `config/safety.yaml` | 规则增删不需要改代码、不需要重新部署 |
| 任务入口 Inbox | Phase 10 才做 | Phase 0 就建 `obsidian/Inbox.md` | Phase 5 一旦 Obsidian 工具就绪即可直接消费,不必等到最后 |
| Memory 快照 | 直接覆盖 profile.md | 每次更新前存入 `memory/history/` | profile 被自动更新跑偏后可回溯排查 |
| Phase 顺序 | 0→1→2→3→5→6→7 | 0→1→2→4→3→6→7 | Memory 提到 Planner 之前:没有上下文的规划质量明显更差,值得优先做 |
| 密钥管理 | 未强调 | `.env` + `.gitignore` 从 Phase 0 起 | 避免密钥被误提交或项目开源后泄露 |

---

## 3. 整体架构

```
                     User
                       |
                       v
      +----------------+----------------+
      |         Input Layer             |
      |  CLI 输入 / obsidian/Inbox.md   |
      +----------------+----------------+
                       |
                       v
               +---------------+
               | PersonalAgent |  agent/agent.py
               +-------+-------+
                       |
         +-------------+-------------+
         |                           |
         v                           v
   +-----------+               +-----------+
   |  Planner  |               |  Memory   |
   |(任务拆解)  |<--------------|(用户画像/  |
   +-----+-----+   携带上下文    | 项目状态)  |
         |                     +-----------+
         v
   +-----------+
   | Executor  |  读取 config/safety.yaml 做风险拦截
   +-----+-----+
         |
         v
   +-----------------------------+
   |            Tools            |
   |  shell | obsidian | (后续:  |
   |  browser | git | task_inbox)|
   +-------------+---------------+
                 |
                 v
        +-----------------+
        | Obsidian Vault  |
        | Daily/ Inbox.md |
        | Projects/ ...   |
        +-----------------+
```

**核心循环(MVP 闭环)**:

```
任务输入(CLI 或 Inbox.md)
   -> Memory 提供上下文
   -> Planner 生成结构化步骤(JSON)
   -> Executor 逐步执行,先过 safety.yaml 检查
   -> Tools 实际执行(shell / obsidian 读写)
   -> 结果汇总写入 obsidian/Daily/{date}.md
```

---

## 4. 模块职责边界

明确划分职责,防止后期调试时无法定位问题出在"规划错了"还是"执行错了":

- **`agent/llm.py`**:唯一的 LLM 调用入口。Agent 内其他模块不得直接调用 DeepSeek/OpenAI SDK,统一走这一层,便于未来切换或多模型路由。
- **`agent/planner.py`**:只负责"自然语言任务 -> 结构化步骤列表",不执行任何实际操作,不调用工具。输出固定为 JSON: `{"goal": ..., "steps": [{"id", "action", "tool", "risk"}]}`。
- **`agent/executor.py`**:只负责"拿到步骤 -> 调用对应工具 -> 收集结果",不做任务拆解。执行前统一过 `safety.yaml` 规则(block / confirm / allow 三态)。
- **`agent/memory.py`**:读写 `memory/profile.md`、`memory/projects.md`。每次写入前把旧版本存进 `memory/history/`,不做覆盖式更新。
- **`tools/*.py`**:每个工具继承 `tools/base.py` 的 `Tool` 抽象类,统一返回 `{"success": bool, "result": ..., "error": ...}`,Executor 不关心工具内部实现。

---

## 5. 安全设计

### 5.1 命令执行风险分级

`config/safety.yaml` 定义三类命令模式:

- **block**:直接拒绝执行(如 `rm -rf`、`shutdown`、fork bomb)
- **confirm**:需要用户二次确认后才执行(如 `git push`、`git reset --hard`、`pip install`)
- **allow**(默认):其余命令正常执行

规则与代码分离,增删规则不需要改动 `executor.py` 或重新部署。

### 5.2 微信/QQ 边界

明确不做:
- 非官方协议登录 / 消息收发自动化(封号风险高且不可控)
- 任何形式的账号模拟操作

明确采用:
- 用户手动将消息内容粘贴至 `obsidian/Inbox.md`
- `tools/task_inbox.py` 读取该文件内容交给 Planner 处理

### 5.3 密钥管理

- API Key 一律放 `.env`,不写入 `config.yaml`
- `.gitignore` 从 Phase 0 起纳入 `.env`、`logs/`、`memory/history/`,避免敏感信息或个人数据被提交到版本控制

---

## 6. 开发阶段规划

### MVP 闭环(必须完成,构成最小可用版本)

| Phase | 目标 | 新增文件 | 完成标准 |
|---|---|---|---|
| 0 | 项目脚手架 | `config/config.yaml`, `config/prompts.yaml`, `config/safety.yaml`, `.env`, `tools/base.py`, `obsidian/Inbox.md` | `uv run main.py` 能启动 CLI |
| 1 | LLM 接入层 | `agent/llm.py` | 输入"你好",能收到模型回复 |
| 2 | Agent 核心控制器 | `agent/agent.py` | 输入任务,`PersonalAgent.run()` 能直接对话返回结果(尚未接入 Planner) |
| 4 | Memory 记忆系统 | `agent/memory.py`, `memory/profile.md`, `memory/projects.md`, `memory/history/` | 对话时自动携带用户画像与项目状态作为上下文 |
| 3 | 任务规划系统 | `agent/planner.py` | 输入复杂任务,输出结构化 JSON 步骤列表 |
| 6 | Tool 系统 | `tools/shell.py`, `tools/obsidian.py`, `tools/task_inbox.py` | Agent 可调用 shell 执行命令并获取 stdout/stderr;可读写 Obsidian |
| 7 | Executor 执行器 | `agent/executor.py` | Planner 输出的步骤能被逐一执行,危险命令按 `safety.yaml` 拦截或要求确认;结果写入 `obsidian/Daily/{date}.md` |

MVP 完成标准:**任务输入 → AI 规划 → 工具执行 → Obsidian 记录**,形成完整闭环。

### 增强阶段(MVP 跑通后按需推进)

| Phase | 目标 | 备注 |
|---|---|---|
| 8 | Browser Agent | 用于论文/资料搜索并总结,技术选型 Playwright 或 Claude in Chrome 一类现成方案 |
| 9 | Git / IDE Agent | 读取 `git status` / `git diff`,辅助代码审查与建议 |
| 10 | 任务入口统一 | 整合 CLI / Inbox.md / 邮件等多来源,`Inbox.md` 本身在 Phase 0 已提前落地 |

### 高级阶段(视实际需求评估,非必须)

| Phase | 目标 | 备注 |
|---|---|---|
| 11 | 长期记忆升级 | Markdown 存储 -> 向量数据库(ChromaDB / Qdrant),用于跨项目历史问答,如"某项目做到哪一步了" |

---

## 7. 目录结构

```
Personal-Agent/
├── main.py
├── pyproject.toml
├── .env                      # 密钥,不入库
├── .gitignore
├── README.md
├── agent/
│   ├── llm.py                # 统一 LLM 调用入口
│   ├── agent.py               # PersonalAgent 主控制器
│   ├── planner.py             # 任务拆解,不执行
│   ├── executor.py            # 步骤执行 + 安全拦截
│   └── memory.py              # 读写 profile / projects,更新前存快照
├── tools/
│   ├── base.py                # Tool 抽象接口
│   ├── obsidian.py            # 读写 Obsidian 笔记
│   ├── shell.py                # 命令行执行
│   └── task_inbox.py          # 读取 Inbox.md 手动任务
├── config/
│   ├── config.yaml             # 模型/路径等常规配置
│   ├── prompts.yaml            # 各模块 system prompt
│   └── safety.yaml             # 危险命令黑名单/确认清单
├── memory/
│   ├── profile.md
│   ├── projects.md
│   └── history/                # 每次更新前的快照,不入库
├── obsidian/
│   ├── Daily/                  # 每日工作日志
│   ├── Projects/
│   ├── Knowledge/
│   └── Inbox.md                # 手动任务投喂入口
└── logs/
```

---

## 8. 未决问题(需要后续明确)

- `memory/profile.md` 的更新触发方式:每日自动从 `Daily/{date}.md` 提炼,还是用户手动维护?两者可以并存,但需要明确"自动更新覆盖用户手改内容"时的处理策略。
- Executor 遇到某一步执行失败时,是否要触发 Planner 重新规划,还是直接终止并汇报用户?建议 MVP 阶段先做"失败即终止 + 记录到日志",重规划留到后续迭代。
- `config.yaml` 中 `agent.max_steps` 的默认值需要根据实际任务复杂度调整,防止规划步骤过多导致执行时间失控。