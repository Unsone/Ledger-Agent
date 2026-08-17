import json
import re
from agent.llm import LLM
from agent.schemas import Plan


class Planner:
    """任务规划器：自然语言 → 结构化步骤列表。

    Phase 3：只负责拆解任务，不执行任何操作。
    后续 Phase 7 由 Executor 消费 Planner 的输出。

    输出可靠性双保险：
    1. LLM JSON mode（response_format）→ 保证合法 JSON
    2. Pydantic Plan 模型校验 → 类型/枚举/工具/步数约束
    """

    # JSON 解析或校验失败时的最大重试次数
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
            ValueError: LLM 返回内容无法解析或校验失败
        """
        system_prompt = self._build_system_prompt()
        user_message = self._build_user_message(task, context)

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ]

        # 校验上下文：运行时约束注入 Pydantic
        validation_context = {
            "valid_tools": set(self.tools_registry.keys()) | {"none"},
            "max_steps": self.max_steps,
        }

        last_error = None
        raw = ""  # 初始化，防止 LLM 调用直接抛异常时 UnboundLocalError
        for attempt in range(self.MAX_RETRIES + 1):
            try:
                raw = self.llm.chat(messages, json_mode=True)
                data = self._parse_response(raw)
                plan = Plan.model_validate(data, context=validation_context)
                return plan.model_dump()
            except (json.JSONDecodeError, ValueError) as e:
                last_error = e
                if attempt < self.MAX_RETRIES:
                    messages.append({"role": "assistant", "content": raw})
                    messages.append(
                        {
                            "role": "user",
                            "content": (
                                f"你上面的输出不是合法的 JSON 或不符合 schema。错误信息：{e}\n"
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
        """根据 tools_registry 生成工具列表描述（含每个 action 的精确参数）。"""
        if not self.tools_registry:
            return "（暂无可用工具，所有步骤的 tool 请填 \"none\"）"

        lines = []
        for name, tool in self.tools_registry.items():
            lines.append(f"- **{name}**: {tool.description}")
            # 附上 params_schema，让 LLM 用精确的参数名
            schema = getattr(tool, "params_schema", None)
            if schema:
                for action, params in schema.items():
                    if not params:
                        lines.append(f"    - {action}: 无参数")
                        continue
                    specs = []
                    for p in params:
                        req = "必填" if p.get("required") else "可选"
                        specs.append(f"{p['name']}({req}): {p.get('desc', '')}")
                    lines.append(f"    - {action}: " + "; ".join(specs))
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
