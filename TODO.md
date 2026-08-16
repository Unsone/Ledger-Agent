# 待办任务

> 优先级：P0 紧急 / P1 重要 / P2 常规 / P3 低优先
> 来源：代码审查 + 架构讨论 + 使用中发现的问题

## P0 — 根治病症

### 1. 工具参数 schema 化
- **问题**：Planner（LLM）发明不存在的参数名（`filepath` vs `path`、`operation` vs `action`），目前靠各工具入口做别名兜底，治标不治本
- **方案**：在 `tools/base.py` 的 Tool 基类声明 `params_schema`（JSON Schema），Planner prompt 自动注入精确参数定义，Executor 执行前校验
- **影响**：消灭参数名错误、减少一次 repair 往返

### 2. 安全匹配器正则化
- **问题**：`safety.yaml` 用子串 + 大小写敏感匹配：
  - 误报：`"format "` 会 block 任何含 "format " 的命令（如 Python `.format()`）
  - 绕过：`RＭ -rf`（全角）、`rm  -rf`（双空格）、`Git PUSH` 均可规避
- **方案**：改为正则匹配 + `re.IGNORECASE`，补充 Windows 别名规则（`rd /s /q`、`erase`）
- **影响**：安全模型可信度

## P1 — 重要功能

### 3. Git 工具（DESIGN Phase 9）
- 结构化封装：`status()`、`diff()`、`log()`、`commit(message)`
- 核心场景：`/run 看看改了什么，帮我提交` → 读 diff → LLM 生成 message → 提交
- 比裸 shell 跑 git 的优势：结构化输出、自动生成 commit message、安全确认集成

### 4. 对话历史截断
- **问题**：`chat()` 的 messages 无限增长，长会话撑爆 LLM 上下文窗口
- **方案**：保留 system prompt + memory 上下文 + 最近 N 轮，超出时自动截断（滑动窗口）

### 5. 结构化输出（Pydantic schema）
- **问题**：Planner 输出靠手工 `_validate` 校验，报错信息粗
- **方案**：用 Pydantic model 定义 Plan/Step，LLM 结构化输出模式（DeepSeek 支持 JSON mode）
- **影响**：更可靠的格式保证、类型安全

### 6. 代码运行工具
- **问题**：现在跑代码只能靠 shell 的 `python xxx.py`，stdout 是纯文本；报错时无法结构化分析、无隔离、无法跑片段
- **方案**：新增 `tools/python_runner.py`：
  - 执行代码片段/脚本，捕获 stdout/stderr/退出码/耗时
  - 超时与输出上限控制
  - 失败时把 traceback 结构化返回，供 Planner 直接据此修复（与自检回路联动）
- **核心场景**：`/run 跑一下 tests/test_tools.py 看哪个用例挂了` → 拿到结构化失败列表 → 自动定位 → file 工具修复 → 再跑
- **影响**：补齐"开发闭环"最后一环（改 → 跑 → 看错 → 再改）

## P2 — 常规改进

### 7. Step Chaining 引号安全
- **问题**：`{step_N_result}` 替换进 Python 字符串时，若结果含引号/三引号会破坏语法
- **方案**：替换前做转义，或改用临时文件传递大数据

### 8. Inbox 自动接入（DESIGN Phase 10）

### 6. Step Chaining 引号安全
- **问题**：`{step_N_result}` 替换进 Python 字符串时，若结果含引号/三引号会破坏语法
- **方案**：替换前做转义，或改用临时文件传递大数据

### 7. Inbox 自动接入（DESIGN Phase 10）
- **问题**：`task_inbox` 工具存在但无 CLI 命令、无自动消费
- **方案**：加 `/inbox` 命令；启动时检测 Inbox 非空提示处理

### 9. RAG 长期记忆（DESIGN Phase 11）
- 向量化 memory 与 Daily 笔记，支持"某项目做到哪一步了"跨会话问答
- 技术选型：ChromaDB（本地零配置）

### 10. Browser Agent（DESIGN Phase 8）
- 超出搜索的浏览器操作：表单填写、页面交互、截图
- 技术选型：Playwright

## P3 — 打磨

### 11. 硬编码值迁移到 config.yaml
- `MAX_RETRIES=3`、`MAX_VERIFY_ROUNDS=2`、shell timeout=120、输出截断 4000/1500/120 等散落代码中

### 12. llm.py 健壮性
- API key 缺失时友好报错（现在是 KeyError 堆栈）
- `response.choices[0].message.content` 的 None 检查
- 多 provider 配置化（base_url / key 名可配，现在是 DeepSeek 硬编码）

### 13. Memory 自动提炼
- DESIGN.md 未决问题：从 Daily 日记自动更新 projects.md 的"最近进展"字段
- 需要先明确与用户手改内容的冲突处理策略

## 已驳回

- ~~整体迁移 LangChain/LangGraph~~：手写架构已覆盖其核心模式（工具接口、规划、记忆、自检），按需引入单组件（RAG 切块/向量库）即可
- ~~微信/QQ 自动化~~：封号风险，采用 Inbox.md 手动投喂（DESIGN 明确）
