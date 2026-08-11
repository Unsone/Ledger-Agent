import json
import re
from agent.llm import LLM


class Planner:
    """任务规划器：自然语言 → 结构化步骤列表。

    Phase 3：只负责拆解任务，不执行任何操作。
    后续 Phase 7 由 Executor 消费 Planner 的输出。
    """

    # JSON 解析失败时的最大重试次数
    MAX_RETRIES = 2

    def __init__(self, llm: LLM, planner_prompt: str, tools_registry: dict = None, max_steps: int = 10):
        """
        Args:
            llm: LLM 实例（共享，不新建）
            planner_prompt: 规划 prompt 模板，含 {tools_description} 占位符
            tools_registry: {"tool_name": ToolInstance, ...}，用于生成工具描述
            max_steps: 单次计划最多允许的步骤数（从 config 读取，默认 10）
        """
        self.llm = llm
        self.prompt_template = planner_prompt
        self.tools_registry = tools_registry or {}
        self.max_steps = max_steps

    def plan(self, task: str, context: str = "") -> dict:
        """将自然语言任务拆解为结构化步骤。

        Args:
            task: 用户的任务描述
            context: 可选的附加上下文（如 memory 信息）

        Returns:
            {
                "goal": "任务目标概括",
                "steps": [
                    {"id": 1, "action": "...", "tool": "shell", "params": {...}, "risk": "low"},
                    ...
                ]
            }

        Raises:
            ValueError: LLM 返回内容无法解析为合法 JSON
        """
        system_prompt = self._build_system_prompt()
        user_message = self._build_user_message(task, context)

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ]

        last_error = None
        raw = ""  # 初始化，防止 LLM 调用直接抛异常时 UnboundLocalError
        for attempt in range(self.MAX_RETRIES + 1):
            try:
                raw = self.llm.chat(messages)
                result = self._parse_response(raw)
                self._validate(result)
                return result
            except (json.JSONDecodeError, ValueError) as e:
                last_error = e
                if attempt < self.MAX_RETRIES:
                    messages.append({"role": "assistant", "content": raw})
                    messages.append(
                        {
                            "role": "user",
                            "content": (
                                f"你上面的输出不是合法的 JSON。错误信息：{e}\n"
                                "请**只输出**正确的 JSON 对象，不要加 markdown 代码块标记。"
                            ),
                        }
                    )
            except Exception as e:
                # LLM 调用本身的错误（网络、API 等），也重试
                last_error = e
                if attempt >= self.MAX_RETRIES:
                    raise ValueError(
                        f"Planner LLM 调用失败（{self.MAX_RETRIES + 1} 次尝试）。最后错误：{e}"
                    )

        raise ValueError(f"Planner 在 {self.MAX_RETRIES + 1} 次尝试后仍无法输出合法 JSON。最后错误：{last_error}")

    def repair(self, task: str, failed_steps: list[dict], context: str = "") -> dict:
        """根据执行错误自动修正计划。

        Args:
            task: 原始任务描述
            failed_steps: 失败的步骤列表，每个包含 action, error 等字段
            context: 可选的附加上下文

        Returns:
            修正后的 plan dict，格式同 plan()
        """
        # 构建错误摘要
        error_lines = ["之前的执行尝试失败了，以下是错误信息：", ""]
        for i, fs in enumerate(failed_steps, 1):
            error_lines.append(
                f"失败步骤 {i}: {fs.get('action', '?')}"
            )
            error_lines.append(f"  工具: {fs.get('tool', '?')}")
            error_lines.append(f"  错误: {fs.get('error', '未知错误')}")
        error_lines.append("")
        error_lines.append("请分析错误原因，修正命令/参数，重新规划。")
        error_lines.append("常见问题：路径不存在（先创建目录）、权限不足、命令拼写错误、引号转义问题。")
        error_lines.append("注意：当前环境是 Windows cmd.exe，不要使用 Unix 命令（如 mkdir -p 应改为 mkdir 逐级创建，ls 应改为 dir，rm 应改为 del 等）。")

        repair_context = "\n".join(error_lines)
        full_context = f"{context}\n\n{repair_context}" if context else repair_context

        return self.plan(task, context=full_context)

    # ── 内部方法 ──────────────────────────────────────────

    def _build_system_prompt(self) -> str:
        """构建 planner 的 system prompt，填入可用工具描述。"""
        tools_desc = self._describe_tools()
        # 用 replace 而非 format()，因为 prompt 模板内含 JSON 示例的花括号
        return self.prompt_template.replace("{tools_description}", tools_desc)

    def _build_user_message(self, task: str, context: str) -> str:
        """构建给 planner 的用户消息。"""
        parts = []
        if context:
            parts.append(f"上下文信息：\n{context}")
        parts.append(f"请规划以下任务：\n{task}")
        return "\n\n".join(parts)

    def _describe_tools(self) -> str:
        """根据 tools_registry 生成工具列表描述。"""
        if not self.tools_registry:
            return "（暂无可用工具，所有步骤的 tool 请填 \"none\"）"

        lines = []
        for name, tool in self.tools_registry.items():
            lines.append(f"- **{name}**: {tool.description}")
        return "\n".join(lines)

    @staticmethod
    def _parse_response(raw: str) -> dict:
        """从 LLM 原始回复中提取 JSON。

        处理 LLM 可能包裹的 ```json ... ``` 代码块。
        """
        raw = raw.strip()

        # 尝试提取 ```json ... ``` 代码块
        m = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", raw, re.DOTALL)
        if m:
            raw = m.group(1).strip()

        return json.loads(raw)

    def _validate(self, result: dict):
        """验证 Planner 输出的 JSON 结构是否符合规范（结构 + 安全 + 步数限制）。"""
        if not isinstance(result, dict):
            raise ValueError("输出必须是 JSON 对象（dict）")

        if "goal" not in result:
            raise ValueError("缺少 'goal' 字段")

        steps = result.get("steps")
        if not isinstance(steps, list) or len(steps) == 0:
            raise ValueError("'steps' 必须是非空数组")

        # 强制 max_steps（config.yaml 配置，默认 10）
        if len(steps) > self.max_steps:
            raise ValueError(
                f"步骤数 {len(steps)} 超过最大限制 {self.max_steps}，请合并或精简"
            )

        valid_risks = {"low", "medium", "high"}
        valid_tools = set(self.tools_registry.keys()) | {"none"}

        for i, step in enumerate(steps):
            if not isinstance(step, dict):
                raise ValueError(f"steps[{i}] 必须是对象")
            for field in ("id", "action", "tool", "risk"):
                if field not in step:
                    raise ValueError(f"steps[{i}] 缺少 '{field}' 字段")

            # 校验 risk 值（防止 typo 如 "highh" 绕过安全检查）
            if step["risk"] not in valid_risks:
                raise ValueError(
                    f"steps[{i}] risk='{step['risk']}' 无效，必须是 low/medium/high"
                )

            # 校验 tool 存在
            if step["tool"] not in valid_tools:
                raise ValueError(
                    f"steps[{i}] tool='{step['tool']}' 不在可用工具列表中 ({', '.join(sorted(valid_tools))})"
                )

            # 自动补齐 params
            if "params" not in step:
                step["params"] = {}
