# Personal-Agent

一个本地运行的个人工作辅助 Agent：将自然语言任务输入，经 LLM 规划后由受安全约束的工具实际执行，并将执行记录沉淀到 Obsidian 知识库。

设计文档见 [DESIGN.md](DESIGN.md)。

---

## 核心能力

```
任务输入 (CLI / Inbox.md)
   → Memory 提供用户画像与项目状态上下文
   → Planner 将自然语言拆解为结构化步骤 (JSON)
   → Executor 逐步骤执行，执行前过 safety.yaml 安全检查
   → Tools 实际执行 (shell / file / git / python_runner / obsidian / web_search / task_inbox)
   → 结果自检：不满足用户意图时自动补救重规划 (最多 2 轮)
   → 执行记录写入配置的笔记库 Daily/{date}.md
```

已实现的关键特性：

- **双层级纠错**：执行层（命令失败 → 自动修复重试，最多 3 次）+ 语义层（结果不足以回答问题 → 自动生成补救任务重新规划，最多 2 轮）
- **开发闭环自动化**：python_runner 执行代码捕获结构化 traceback → 自动定位错误 → file 工具精确修复 → 重新运行验证（改-跑-看错-再改全自动）
- **步骤间数据传递**：后续步骤可用 `{step_N_result}` 占位符引用前序步骤的实际输出
- **三态安全检查**：`block`（直接拒绝）/ `confirm`（需用户确认）/ `allow`，规则配置在 `config/safety.yaml`，与代码分离，覆盖 shell 与 git 等非 shell 工具
- **Memory 快照**：用户画像与项目状态每次更新前自动存入 `memory/history/`，可回溯排查
- **命令审计日志**：所有 shell 命令执行记录到 `logs/agent.log`，按天轮转
- **RAG 长期记忆**：将 `memory/` 与 Obsidian 笔记增量向量化，支持跨会话检索与问答；模型不可用时自动降级为本地关键词检索

---

## 架构

```
Personal-Agent/
├── main.py                 # CLI 入口
├── agent/
│   ├── agent.py            # 主控制器：对话循环、/run 管线、自检回路
│   ├── llm.py              # 唯一 LLM 调用入口（OpenAI 兼容 SDK）
│   ├── planner.py          # 任务拆解：NL → {goal, steps[]}，含结构校验
│   ├── executor.py         # 步骤执行：安全检查 → 工具调度 → 结果收集
│   ├── memory.py           # 用户画像/项目状态读写，写前快照
│   ├── rag.py              # 长期记忆：切块、增量索引、语义检索与问答
│   └── logger.py           # 统一日志，按天轮转
├── tools/
│   ├── base.py             # Tool 抽象接口：统一返回 {success, result, error}
│   ├── shell.py            # 命令行执行（UTF-8 处理、$HOME 展开、输出截断）
│   ├── file.py             # 文件读写与精确编辑（带行号 read、唯一匹配 edit）
│   ├── git.py              # Git 版本控制（结构化 status/diff/log/add/commit/push）
│   ├── python_runner.py    # Python 执行（子进程隔离、超时、结构化 traceback）
│   ├── obsidian.py         # Obsidian vault 读写（含路径穿越防护）
│   ├── web_search.py       # DuckDuckGo 搜索 + 网页内容抓取
│   ├── task_inbox.py       # 手动任务投喂（Inbox.md 读取与归档）
│   └── memory_search.py    # RAG 记忆库检索工具
├── config/
│   ├── config.yaml         # LLM 参数、agent 参数
│   ├── prompts.yaml        # 各模块 system prompt（版本化、可调优）
│   └── safety.yaml         # 危险命令规则（block/confirm 模式列表）
├── memory/                 # 用户画像、项目状态、历史快照
├── obsidian/               # 默认笔记库：Daily/、Projects/、Knowledge/、Inbox.md
├── tests/                  # 117 个 pytest 用例（无需 API key）
└── logs/                   # 运行日志（不入库）
```

### 模块职责边界

| 模块 | 只负责 | 不负责 |
|---|---|---|
| `llm.py` | LLM API 调用 | 业务逻辑 |
| `planner.py` | NL → JSON 步骤列表 | 执行任何操作 |
| `executor.py` | 步骤分发 + 安全检查 | 任务拆解 |
| `tools/*` | 单一能力的原子操作 | 流程编排 |
| `agent.py` | 流程编排与交互 | 具体执行 |

---

## 任务执行管线

`/run <任务描述>` 的完整流程：

```
_handle_run(task)
  │
  ├─ 1. Planner.plan(task, context=memory)     # 携带用户画像规划
  │
  ├─ 2. 用户确认（/run -y 跳过）
  │
  ├─ 3. Executor.execute_plan(plan)
  │     ├─ 每步骤：_expand_step_refs   # {step_N_result} 占位符替换
  │     ├─ 每步骤：_check_safety       # block > confirm > allow
  │     ├─ block   → 拒绝，记录日志，后续步骤不受影响
  │     ├─ confirm → 用户确认回调（CLI 交互 / 可注入）
  │     └─ 失败    → 自动纠错重试（Planner.repair，最多 3 次）
  │
  ├─ 4. _verify_and_synthesize          # 语义自检
  │     ├─ LLM 综合所有轮次结果 → 自然语言回答
  │     ├─ LLM 自检：是否满足用户意图？
  │     └─ 不满足 → 生成补救任务 → 回到步骤 1（最多 2 轮）
  │
  └─ 5. _record_execution               # 写入 obsidian/Daily/{date}.md
```

### 安全模型

```
检查顺序（优先级从高到低）：
1. safety.yaml blocked_patterns  → block   如 "rm -rf"、"shutdown"
2. safety.yaml confirm_patterns  → confirm 如 "git push"、"pip install"
3. Planner 标注 risk=high        → confirm 自动升级
4. 其他                          → allow
```

设计要点：

- 规则与代码分离：增删规则只改 `config/safety.yaml`，无需改代码
- `block`/用户取消不会触发 stop_on_failure——安全决策不是执行失败
- Obsidian 工具双重路径穿越防护（拒绝绝对路径 + `relative_to` 校验）
- File 工具的 `edit` 操作要求 `old_text` 唯一匹配，防止误改
- 所有命令执行写入审计日志

---

## 快速开始

```bash
# 1. 安装依赖（需要 Python >= 3.13）
uv sync

# 2. 配置 API key
cp .env.example .env        # 然后填入 DEEPSEEK_API_KEY

# 3. （可选）连接外部笔记目录：编辑 config/config.local.yaml 中 notes.vault_path
#    例如 Hexo：D:/Projects/my-hexo/source/_posts

# 4. （可选）填写用户画像
# 编辑 memory/profile.md 和 memory/projects.md

# 5. 启动
uv run main.py
```

CLI 命令：

```
exit / quit      退出
clear            清空对话历史（保留 memory 上下文）
/memory          查看注入的记忆上下文
/refresh         重新加载 memory 文件
/ask <问题>       检索长期记忆并基于相关笔记回答
/plan <任务>     仅规划，不执行
/run <任务>      规划 + 执行 + 自检 + 记录
/run -y <任务>   跳过所有确认
```

### 长期记忆问答

使用 `/ask` 查询 `memory/` 和配置笔记库下的 Markdown 笔记。例如：

```text
/ask Alpha 项目目前做到哪一步？
```

首次查询会建立本地索引；之后仅处理新增、修改或删除的笔记。笔记库根目录中的 `Inbox.md` 不会被索引，避免把临时待办当作长期知识。向量索引位于 `memory/vector_store/`，属于本地运行数据，不会提交到 Git。

### 连接 Hexo 或外部笔记库

在 `config/config.local.yaml` 填写 `notes.vault_path` 即可让 Agent 使用外部目录作为笔记库：

```yaml
notes:
  vault_path: "D:/Projects/my-hexo/source/_posts"
```

路径可以是绝对路径，也可以是相对于本项目根目录的路径。填写后，`obsidian` 工具、`/ask`、`Inbox.md` 和自动执行记录都会使用该目录；工具调用时的笔记路径仍须相对于这个根目录，例如 `path: "2026-08-18-note.md"`。目录必须预先存在，留空则继续使用项目内的 `obsidian/`。

`config.local.yaml` 会在启动时覆盖同名的 `config.yaml` 字段，并已被 Git 忽略；请将本机绝对路径、私有服务地址等个人配置放在这里，不要写入公开的 `config.yaml`。

---

## 技术要点

### Windows 兼容性处理

本项目在 Windows + cmd.exe 环境下开发，以下问题已在工具层解决：

- `subprocess` 输出编码强制 UTF-8，`chcp 65001` 前缀切换代码页
- `$HOME` / `%USERPROFILE%` 在执行前预展开（cmd 不识别 `$VAR` 语法）
- 展开后反斜杠转正斜杠，避免 `C:\Users\...` 中 `\U` 被 Python 误解析为 Unicode 转义
- File 工具统一内部 LF，`newline=""` 阻止 Windows 的 CRLF 自动翻译
- 终端输出 `sys.stdout.reconfigure(encoding="utf-8")` 解决 GBK 控制台乱码

### LLM 输出可靠性

三层保障确保 Planner 输出结构可靠：

1. **JSON mode**：LLM 调用启用 `response_format={"type": "json_object"}`，从源头保证合法 JSON
2. **Pydantic 校验**：`agent/schemas.py` 的 Plan/Step 模型做类型化校验（字段必填、risk 枚举、tool 存在性、max_steps 上限均通过 validation context 注入）
3. **重试回路**：解析或校验失败自动带错误信息重试（最多 2 次），LLM 调用异常（网络/API）同样纳入重试

### 工具参数 Schema

每个工具在 `params_schema` 中声明各 action 的精确参数（名称/必填/说明），形成双保险：

1. **规划前**：Planner 的 prompt 自动注入全部参数定义，LLM 直接生成正确参数名
2. **执行前**：Executor 校验必填参数，缺失时报错并提示正确参数名（触发自动修复回路）

此机制从根源上解决 LLM 发明参数名的问题（如 `filepath` vs `path`）；各工具的别名兼容仍保留作为兜底。

### 测试

129 个 pytest 用例，全部可在无 API key、无网络环境下运行：

```bash
uv run pytest tests/ -v
```

- LLM 调用通过 mock 注入
- 文件操作用临时目录隔离
- Git 测试使用临时 git 仓库
- 覆盖安全三态、路径穿越回归、快照去重、CRLF 处理、超时与隔离等关键边界

---

## 路线图

| 优先级 | 项目 | 说明 |
|---|---|---|
| P0 | 安全匹配器正则化 | 解决子串匹配误报与绕过 |
| P1 | 对话历史截断 | 防止长会话撑爆上下文窗口 |
| 已完成 | RAG 长期记忆（Phase 11） | 向量数据库跨项目问答（`/ask` 与 `memory_search`） |
| P3 | Browser Agent（Phase 8） | 浏览器交互，超出搜索的网页操作 |

---

## 技术栈

- Python 3.13+
- [uv](https://github.com/astral-sh/uv) 包管理
- OpenAI 兼容 SDK（DeepSeek API）
- [ddgs](https://github.com/ddgth/ddgth) DuckDuckGo 搜索
- pytest 测试框架
- Obsidian（本地 Markdown vault）

## License

MIT
