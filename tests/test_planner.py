"""Planner 测试：JSON 解析、结构校验、repair 方法。"""

import pytest
import json
from agent.planner import Planner


@pytest.fixture
def planner(mock_llm):
    """创建一个 Planner 实例（LLM 用 mock）。"""
    return Planner(
        llm=mock_llm,
        planner_prompt="测试 prompt。可用工具：{tools_description}",
        tools_registry={"shell": type("obj", (), {"description": "执行命令"})()},
        max_steps=10,
    )


class TestJSONParsing:
    """_parse_response：从 LLM 原始回复中提取 JSON。"""

    def test_plain_json(self, planner):
        """纯 JSON 文本。"""
        raw = '{"goal": "测试", "steps": [{"id": 1, "action": "test", "tool": "shell", "risk": "low"}]}'
        result = planner._parse_response(raw)
        assert result["goal"] == "测试"
        assert len(result["steps"]) == 1

    def test_fenced_json(self, planner):
        """)```json ... ``` 包裹的 JSON。"""
        raw = '```json\n{"goal": "测试", "steps": [{"id": 1, "action": "t", "tool": "none", "risk": "low"}]}\n```'
        result = planner._parse_response(raw)
        assert result["goal"] == "测试"

    def test_fenced_no_lang(self, planner):
        """``` 包裹的 JSON（无语言标记）。"""
        raw = '```\n{"goal": "x", "steps": [{"id": 1, "action": "y", "tool": "none", "risk": "low"}]}\n```'
        result = planner._parse_response(raw)
        assert result["goal"] == "x"

    def test_invalid_json_raises(self, planner):
        """无效 JSON 应抛出异常。"""
        with pytest.raises(json.JSONDecodeError):
            planner._parse_response("这不是 JSON")

    def test_strips_whitespace(self, planner):
        """前后空白应被去除。"""
        raw = '  \n  {"goal": "g", "steps": [{"id": 1, "action": "a", "tool": "none", "risk": "low"}]}  \n  '
        result = planner._parse_response(raw)
        assert result["goal"] == "g"


class TestValidation:
    """_validate：结构校验 + 步数限制 + risk 校验 + tool 校验。"""

    def valid_plan(self):
        return {
            "goal": "测试目标",
            "steps": [
                {"id": 1, "action": "步骤1", "tool": "shell", "params": {}, "risk": "low"}
            ],
        }

    def test_valid_plan_passes(self, planner):
        """合法的 plan 应通过校验。"""
        planner._validate(self.valid_plan())  # 不应抛异常

    def test_missing_goal(self, planner):
        """缺少 goal 字段。"""
        plan = self.valid_plan()
        del plan["goal"]
        with pytest.raises(ValueError, match="goal"):
            planner._validate(plan)

    def test_empty_steps(self, planner):
        """steps 为空数组。"""
        plan = self.valid_plan()
        plan["steps"] = []
        with pytest.raises(ValueError, match="非空"):
            planner._validate(plan)

    def test_missing_field_in_step(self, planner):
        """步骤缺少必填字段。"""
        plan = self.valid_plan()
        del plan["steps"][0]["tool"]
        with pytest.raises(ValueError, match="tool"):
            planner._validate(plan)

    def test_invalid_risk_value(self, planner):
        """无效的 risk 值应被拒绝。"""
        plan = self.valid_plan()
        plan["steps"][0]["risk"] = "critical"  # 不是 low/medium/high
        with pytest.raises(ValueError, match="risk"):
            planner._validate(plan)

    def test_valid_risk_values(self, planner):
        """low/medium/high 都应通过。"""
        for risk in ("low", "medium", "high"):
            plan = self.valid_plan()
            plan["steps"][0]["risk"] = risk
            planner._validate(plan)  # 不应抛异常

    def test_invalid_tool_name(self, planner):
        """不在 registry 中的 tool 应被拒绝。"""
        plan = self.valid_plan()
        plan["steps"][0]["tool"] = "nonexistent_tool"
        with pytest.raises(ValueError, match="tool"):
            planner._validate(plan)

    def test_none_tool_allowed(self, planner):
        """tool=none 应通过校验。"""
        plan = self.valid_plan()
        plan["steps"][0]["tool"] = "none"
        planner._validate(plan)  # 不应抛异常

    def test_auto_fill_params(self, planner):
        """缺少 params 字段时自动补齐。"""
        plan = self.valid_plan()
        del plan["steps"][0]["params"]
        planner._validate(plan)
        assert "params" in plan["steps"][0]
        assert plan["steps"][0]["params"] == {}

    def test_max_steps_exceeded(self, planner):
        """超过 max_steps 应被拒绝。"""
        plan = {
            "goal": "太多步骤",
            "steps": [
                {"id": i, "action": f"步骤{i}", "tool": "none", "params": {}, "risk": "low"}
                for i in range(1, 12)  # 11 步 > max_steps=10
            ],
        }
        with pytest.raises(ValueError, match="步骤数"):
            planner._validate(plan)

    def test_exact_max_steps_allowed(self, planner):
        """恰好 max_steps 步应通过。"""
        plan = {
            "goal": "刚好",
            "steps": [
                {"id": i, "action": f"步骤{i}", "tool": "none", "params": {}, "risk": "low"}
                for i in range(1, 11)  # 10 步 = max_steps
            ],
        }
        planner._validate(plan)  # 不应抛异常


class TestRepair:
    """repair：根据执行错误生成修正计划。"""

    def test_repair_returns_valid_json(self, planner, mock_llm):
        """repair 应返回合法 JSON（通过校验）。"""
        valid_response = json.dumps({
            "goal": "修复后的计划",
            "steps": [{"id": 1, "action": "重试", "tool": "shell", "params": {}, "risk": "low"}],
        }, ensure_ascii=False)
        mock_llm.next_response = valid_response

        failed = [
            {"action": "创建文件", "tool": "shell",
             "error": "目录不存在: C:/bad/path"},
        ]
        result = planner.repair("创建文件", failed)
        assert result["goal"] == "修复后的计划"
        assert mock_llm.call_count >= 1

    def test_repair_includes_error_context(self, planner, mock_llm):
        """repair 的 prompt 应包含错误信息。"""
        mock_llm.next_response = json.dumps({
            "goal": "x",
            "steps": [{"id": 1, "action": "x", "tool": "shell", "params": {}, "risk": "low"}],
        }, ensure_ascii=False)

        planner.repair("原始任务", [{"action": "失败", "tool": "shell", "error": "权限不足"}])
        # 检查 prompt 中是否包含错误信息
        user_message = mock_llm.messages_history[0][1]["content"]
        assert "权限不足" in user_message
        assert "失败" in user_message
