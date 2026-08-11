import re
import yaml
from pathlib import Path


class Executor:
    """步骤执行器：把 Planner 输出的步骤逐一分发给对应工具，过安全拦截。

    Phase 7：连接 Planner → Safety → Tools，MVP 闭环的最后一块拼图。
    """

    def __init__(
        self,
        tools: dict,
        safety_config_path: str = None,
        confirm_callback=None,
        stop_on_failure: bool = True,
    ):
        """
        Args:
            tools: {"tool_name": ToolInstance, ...}
            safety_config_path: safety.yaml 路径
            confirm_callback: fn(command: str) -> bool，用于 CLI 交互确认
            stop_on_failure: 某一步失败后是否终止后续步骤
        """
        self.tools = tools

        if safety_config_path is None:
            safety_config_path = Path(__file__).parent.parent / "config" / "safety.yaml"

        with open(safety_config_path, "r", encoding="utf-8") as f:
            self.safety = yaml.safe_load(f)

        # 确认回调：默认全部通过（非交互模式下使用）
        self.confirm_callback = confirm_callback or (lambda cmd: True)
        self.stop_on_failure = stop_on_failure

    # ── 对外接口 ──────────────────────────────────────────

    def execute_plan(self, plan: dict, auto_confirm: bool = False) -> dict:
        """执行完整计划的所有步骤。

        Args:
            plan: Planner 输出的 {"goal": ..., "steps": [...]}
            auto_confirm: True 时跳过所有 confirm 询问

        Returns:
            {
                "goal": "任务目标",
                "completed": bool,      # 是否全部成功
                "total": int,           # 总步骤数
                "success_count": int,   # 成功数
                "results": [            # 每步详情
                    {"step_id": 1, "action": "...", "tool": "shell",
                     "success": True, "result": "...", "error": None, "verdict": "allow"},
                    ...
                ]
            }
        """
        results = []
        step_context = {}  # {"1": "result string", "2": "result string"}

        for step in plan.get("steps", []):
            # 展开前序步骤的结果引用（{step_N_result} → 实际值）
            step = self._expand_step_refs(step, step_context)

            result = self.execute_step(step, auto_confirm=auto_confirm)
            results.append(result)

            # 成功执行的步骤，将其结果存入上下文供后续步骤引用
            step_key = str(step.get("id", len(results)))
            if result["success"] and result["result"] is not None:
                step_context[step_key] = str(result["result"])

            # 判断是否为"真失败"（block/skip 是安全决策，不算执行失败）
            is_terminal_failure = (
                not result["success"]
                and result["verdict"] not in ("block", "skip")
            )

            if is_terminal_failure and self.stop_on_failure:
                # 标记后续步骤为跳过
                for remaining in plan["steps"][len(results):]:
                    results.append(
                        {
                            "step_id": remaining["id"],
                            "action": remaining.get("action", ""),
                            "tool": remaining.get("tool", "none"),
                            "success": False,
                            "result": None,
                            "error": "前序步骤失败，已跳过",
                            "verdict": "skip",
                        }
                    )
                break

        success_count = sum(1 for r in results if r["success"])

        return {
            "goal": plan.get("goal", ""),
            "completed": success_count == len(results) and success_count > 0,
            "total": len(plan.get("steps", [])),
            "success_count": success_count,
            "results": results,
        }

    def execute_step(self, step: dict, auto_confirm: bool = False) -> dict:
        """执行单个步骤。

        流程：提取 tool → 安全检查 → 确认(如需) → 调度执行
        """
        step_id = step.get("id", "?")
        action = step.get("action", "")
        tool_name = step.get("tool", "none")
        params = step.get("params", {})
        risk = step.get("risk", "low")

        base = {
            "step_id": step_id,
            "action": action,
            "tool": tool_name,
            "success": False,
            "result": None,
            "error": None,
            "verdict": "allow",
        }

        # ── tool=none: 纯信息/思考步骤，跳过 ──
        if tool_name == "none":
            base["success"] = True
            base["result"] = "（无需工具执行）"
            return base

        # ── 工具是否存在 ──
        tool = self.tools.get(tool_name)
        if tool is None:
            base["error"] = f"未知工具: {tool_name}"
            return base

        # ── 安全检查 ──
        verdict = self._check_safety(tool_name, params, risk)
        base["verdict"] = verdict

        if verdict == "block":
            base["error"] = f"命令被安全策略阻止: {params.get('command', params.get('path', str(params)))}"
            return base

        if verdict == "confirm" and not auto_confirm:
            # 提取可读的命令描述给用户确认
            confirm_msg = self._describe_step(tool_name, action, params)
            if not self.confirm_callback(confirm_msg):
                base["error"] = "用户取消执行"
                base["verdict"] = "skip"
                return base

        # ── 执行 ──
        try:
            result = tool.execute(**params)
            base["success"] = result.get("success", False)
            base["result"] = result.get("result")
            base["error"] = result.get("error")
        except Exception as e:
            base["success"] = False
            base["error"] = str(e)

        return base

    # ── 安全检查 ──────────────────────────────────────────

    def _check_safety(self, tool_name: str, params: dict, planner_risk: str) -> str:
        """检查一个步骤的安全等级。

        优先级：block > confirm > allow
        检查来源：
          1. safety.yaml 的 blocked_patterns / confirm_patterns
          2. Planner 标注的 risk 等级（high → confirm）

        Returns:
            "block" | "confirm" | "allow"
        """
        # 只对 shell 工具做命令模式匹配
        command = params.get("command", "")

        # 1. 检查 blocked_patterns（最高优先级）
        for pattern in self.safety.get("blocked_patterns", []):
            if pattern in command:
                return "block"

        # 2. 检查 confirm_patterns
        for pattern in self.safety.get("confirm_patterns", []):
            if pattern in command:
                return "confirm"

        # 3. Planner 标注的 high risk → 升级为 confirm
        if planner_risk == "high":
            return "confirm"

        return "allow"

    # ── 步骤引用展开 ──────────────────────────────────────

    @staticmethod
    def _expand_step_refs(step: dict, step_context: dict[str, str]) -> dict:
        """展开步骤中的 {step_N_result} 占位符，替换为前序步骤的实际输出。

        支持在 params 的所有字符串值、以及 action 文本中做替换。
        占位符格式：{step_1_result} 引用步骤 1 的输出。
        """
        if not step_context:
            return step

        # 构建替换映射：{step_1_result} → 实际值
        replacements = {}
        for step_id, value in step_context.items():
            replacements[f"{{step_{step_id}_result}}"] = value
            # 截断版本：最多 500 字符，避免命令过长
            if len(value) > 500:
                replacements[f"{{step_{step_id}_result}}"] = value[:500] + "..."

        # 替换 action
        action = step.get("action", "")
        for placeholder, value in replacements.items():
            action = action.replace(placeholder, value)
        step = dict(step)  # 浅拷贝，避免修改原始 plan
        step["action"] = action

        # 替换 params 中的所有字符串值
        params = step.get("params", {})
        if params:
            new_params = {}
            for key, val in params.items():
                if isinstance(val, str):
                    for placeholder, value in replacements.items():
                        val = val.replace(placeholder, value)
                new_params[key] = val
            step["params"] = new_params

        return step

    # ── 辅助方法 ──────────────────────────────────────────

    @staticmethod
    def _describe_step(tool_name: str, action: str, params: dict) -> str:
        """生成步骤的可读描述，用于 CLI 确认提示。"""
        if tool_name == "shell":
            cmd = params.get("command", "?")
            return f"[shell] {action}\n  命令: {cmd}"
        elif tool_name == "obsidian":
            op = params.get("action", "?")
            path = params.get("path", "?")
            return f"[obsidian] {action}\n  操作: {op} → {path}"
        else:
            return f"[{tool_name}] {action}"
