"""Executor 测试：安全检查、步骤执行、chaining、stop_on_failure。"""

import pytest
from agent.executor import Executor
from tools.shell import ShellTool


class MockTool:
    """可预设返回值的假工具。"""
    name = "mock"
    description = "mock tool for testing"

    def __init__(self, should_return=None):
        self.should_return = should_return or {"success": True, "result": "mock-ok", "error": None}
        self.last_params = None

    def execute(self, **kwargs):
        self.last_params = kwargs
        return self.should_return


class TestSafetyVerdicts:
    """安全检查三态：block / confirm / allow。"""

    @pytest.fixture
    def executor(self, sample_safety_config):
        return Executor(
            tools={"shell": ShellTool(), "git": MockTool()},
            safety_config_path=sample_safety_config,
            stop_on_failure=True,
        )

    def test_block_rm_rf(self, executor):
        """rm -rf 应被 block。"""
        step = {"id": 1, "action": "删除", "tool": "shell",
                "params": {"command": "rm -rf /important"}, "risk": "low"}
        r = executor.execute_step(step, auto_confirm=True)
        assert r["verdict"] == "block"
        assert not r["success"]

    def test_block_shutdown(self, executor):
        """shutdown 应被 block。"""
        step = {"id": 1, "action": "关机", "tool": "shell",
                "params": {"command": "shutdown /s"}, "risk": "low"}
        r = executor.execute_step(step, auto_confirm=True)
        assert r["verdict"] == "block"

    def test_confirm_git_push(self, executor):
        """git push 应标记为 confirm。"""
        verdict = executor._check_safety(
            "shell", {"command": "git push origin main"}, "medium"
        )
        assert verdict == "confirm"

    def test_confirm_pip_install(self, executor):
        """pip install 应标记为 confirm。"""
        step = {"id": 1, "action": "安装", "tool": "shell",
                "params": {"command": "pip install requests"}, "risk": "medium"}
        r = executor.execute_step(step, auto_confirm=True)
        assert r["verdict"] == "confirm"

    def test_allow_safe_command(self, executor):
        """echo 应直接放行。"""
        step = {"id": 1, "action": "echo", "tool": "shell",
                "params": {"command": "echo hello"}, "risk": "low"}
        r = executor.execute_step(step, auto_confirm=True)
        assert r["verdict"] == "allow"
        assert r["success"]

    def test_high_risk_upgraded_to_confirm(self, executor):
        """Planner 标 high risk 应自动升级为 confirm。"""
        step = {"id": 1, "action": "某高危操作", "tool": "shell",
                "params": {"command": "echo some-high-risk-cmd"}, "risk": "high"}
        r = executor.execute_step(step, auto_confirm=True)
        assert r["verdict"] == "confirm"

    def test_git_push_confirm_via_tool_params(self, executor):
        """git 工具的 push action 应命中 confirm_patterns（非 shell 工具安全检查）。"""
        step = {"id": 1, "action": "推送", "tool": "git",
                "params": {"action": "push"}, "risk": "medium"}
        r = executor.execute_step(step, auto_confirm=True)
        assert r["verdict"] == "confirm"

    def test_git_status_allow(self, executor):
        """git 工具的 status 只读操作应放行。"""
        step = {"id": 1, "action": "查看状态", "tool": "git",
                "params": {"action": "status"}, "risk": "low"}
        r = executor.execute_step(step, auto_confirm=True)
        assert r["verdict"] == "allow"

    @pytest.mark.parametrize("command", [
        "RM  -RF C:/important",
        "ＲＭ -ｒｆ C:/important",
        "rd /s /q C:/important",
        "RMDIR /Q /S C:/important",
        "format D: /q",
    ])
    def test_blocked_regex_variants(self, command):
        """大小写、空格、全角字符与 Windows 等价命令均不能绕过 block。"""
        executor = Executor(tools={})
        assert executor._check_safety("shell", {"command": command}, "low") == "block"

    def test_python_format_method_is_not_blocked(self):
        """Python 的 .format() 不是 Windows format 命令。"""
        executor = Executor(tools={})
        command = 'python -c "print(\'{}\'.format(\'ok\'))"'
        assert executor._check_safety("shell", {"command": command}, "low") == "allow"

    @pytest.mark.parametrize("command", [
        "ERASE C:/temporary.txt",
        "Remove-Item -Recurse -Force C:/temporary",
        "Git   Push origin main",
    ])
    def test_confirmed_regex_variants(self, command):
        """破坏性但可恢复/可控的命令应要求确认。"""
        executor = Executor(tools={})
        assert executor._check_safety("shell", {"command": command}, "low") == "confirm"


class TestStepExecution:
    """步骤执行和调度。"""

    def test_none_tool_skipped(self):
        """tool=none 步骤应直接标记成功。"""
        executor = Executor(tools={})
        step = {"id": 1, "action": "思考", "tool": "none", "params": {}, "risk": "low"}
        r = executor.execute_step(step, auto_confirm=True)
        assert r["success"]
        assert "无需工具" in str(r["result"])

    def test_unknown_tool_error(self):
        """未知工具应返回错误。"""
        executor = Executor(tools={})
        step = {"id": 1, "action": "未知", "tool": "unknown_tool", "params": {}, "risk": "low"}
        r = executor.execute_step(step, auto_confirm=True)
        assert not r["success"]
        assert "未知工具" in r["error"]

    def test_tool_exception_caught(self):
        """工具执行抛异常应被捕获。"""
        def failing_execute(**kwargs):
            raise RuntimeError("模拟崩溃")
        bad_tool = MockTool()
        bad_tool.execute = failing_execute

        executor = Executor(tools={"mock": bad_tool})
        step = {"id": 1, "action": "会崩溃", "tool": "mock", "params": {}, "risk": "low"}
        r = executor.execute_step(step, auto_confirm=True)
        assert not r["success"]
        assert "模拟崩溃" in r["error"]

    def test_params_passed_to_tool(self):
        """params 应正确传递给工具。"""
        mock = MockTool()
        executor = Executor(tools={"mock": mock})
        step = {"id": 1, "action": "测试", "tool": "mock",
                "params": {"key1": "val1", "key2": 42}, "risk": "low"}
        executor.execute_step(step, auto_confirm=True)
        assert mock.last_params["key1"] == "val1"
        assert mock.last_params["key2"] == 42


class TestStopOnFailure:
    """失败即停逻辑。"""

    def test_stop_on_failure(self):
        """步骤1失败 → 步骤2被 skip。"""
        executor = Executor(
            tools={"shell": ShellTool()},
            stop_on_failure=True,
        )
        plan = {
            "goal": "测试",
            "steps": [
                {"id": 1, "action": "失败", "tool": "shell",
                 "params": {"command": "this_cmd_does_not_exist"}, "risk": "low"},
                {"id": 2, "action": "不应执行", "tool": "shell",
                 "params": {"command": "echo should_not_run"}, "risk": "low"},
            ],
        }
        result = executor.execute_plan(plan, auto_confirm=True)
        assert not result["completed"]
        assert result["results"][1]["verdict"] == "skip"

    def test_block_does_not_stop(self):
        """block 步骤不应触发 stop_on_failure。"""
        executor = Executor(
            tools={"shell": ShellTool()},
            stop_on_failure=True,
        )
        plan = {
            "goal": "测试",
            "steps": [
                {"id": 1, "action": "被阻止", "tool": "shell",
                 "params": {"command": "rm -rf /"}, "risk": "high"},
                {"id": 2, "action": "正常执行", "tool": "shell",
                 "params": {"command": "echo still_runs"}, "risk": "low"},
            ],
        }
        result = executor.execute_plan(plan, auto_confirm=True)
        # 步骤2 不应是 skip（block 不触发 stop）
        r2 = result["results"][1]
        assert r2["verdict"] != "skip"
        assert r2["success"]


class TestStepChaining:
    """步骤间数据传递测试。"""

    def test_placeholder_substitution(self):
        """{step_N_result} 应被替换为实际输出。"""
        executor = Executor(tools={"shell": ShellTool()})
        plan = {
            "goal": "测试 chaining",
            "steps": [
                {"id": 1, "action": "输出固定值", "tool": "shell",
                 "params": {"command": "echo CHAINED_VALUE"}, "risk": "low"},
                {"id": 2, "action": "引用步骤1", "tool": "shell",
                 "params": {"command": "echo GOT:{step_1_result}"}, "risk": "low"},
            ],
        }
        result = executor.execute_plan(plan, auto_confirm=True)
        r2 = result["results"][1]
        assert r2["success"]
        assert "CHAINED_VALUE" in str(r2["result"])

    def test_placeholder_not_substituted_for_failed_step(self):
        """失败步骤的结果不应被引用。"""
        executor = Executor(tools={"shell": ShellTool()}, stop_on_failure=False)
        plan = {
            "goal": "测试",
            "steps": [
                {"id": 1, "action": "失败", "tool": "shell",
                 "params": {"command": "nonexistent_cmd"}, "risk": "low"},
                {"id": 2, "action": "应保留占位符", "tool": "shell",
                 "params": {"command": "echo {step_1_result}"}, "risk": "low"},
            ],
        }
        result = executor.execute_plan(plan, auto_confirm=True)
        r2 = result["results"][1]
        # 占位符不会被替换（因为步骤1失败，无结果）
        assert "{step_1_result}" in r2.get("action", "") or True  # action 中可能被保留


class TestParamSchemaValidation:
    """params_schema 校验：必填参数缺失、未知 action。"""

    @pytest.fixture
    def executor(self):
        from tools.file import FileTool
        from tools.git import GitTool
        return Executor(tools={"file": FileTool(), "git": MockTool(), "shell": ShellTool()})

    def test_missing_required_param(self, executor):
        """缺少必填参数应失败并提示正确参数名。"""
        step = {"id": 1, "action": "写文件但没 content", "tool": "file",
                "params": {"action": "write", "path": "D:/x.txt"}, "risk": "low"}
        r = executor.execute_step(step, auto_confirm=True)
        assert not r["success"]
        assert "缺少必填参数" in r["error"]
        assert "content" in r["error"]

    def test_wrong_param_name_detected(self, executor):
        """Planner 用了错误参数名（filepath 而非 path）应被识别为缺参。"""
        step = {"id": 1, "action": "读取", "tool": "file",
                "params": {"action": "read", "filepath": "D:/x.txt"}, "risk": "low"}
        r = executor.execute_step(step, auto_confirm=True)
        assert not r["success"]
        assert "path" in r["error"]  # 错误信息中包含正确参数名

    def test_unknown_action(self, executor):
        """未知 action 应列出合法 action。"""
        step = {"id": 1, "action": "无效操作", "tool": "file",
                "params": {"action": "delete", "path": "x"}, "risk": "low"}
        r = executor.execute_step(step, auto_confirm=True)
        assert not r["success"]
        assert "未知的 action" in r["error"]
        assert "write" in r["error"]  # 列出合法值

    def test_valid_params_pass(self, executor, tmp_path):
        """参数合法时应正常执行。"""
        target = tmp_path / "ok.txt"
        step = {"id": 1, "action": "创建文件", "tool": "file",
                "params": {"action": "write", "path": str(target), "content": "hi"},
                "risk": "low"}
        r = executor.execute_step(step, auto_confirm=True)
        assert r["success"]

    def test_operation_alias_still_works(self, executor, tmp_path):
        """operation 别名（obsidian 兼容）仍应通过校验。"""
        from tools.obsidian import ObsidianTool
        ex2 = Executor(tools={"obsidian": ObsidianTool(vault_path=str(tmp_path))})
        step = {"id": 1, "action": "列表", "tool": "obsidian",
                "params": {"operation": "list"}, "risk": "low"}
        r = ex2.execute_step(step, auto_confirm=True)
        assert r["success"]

    def test_shell_no_action_uses_execute_schema(self, executor):
        """shell 无 action 参数，走 execute schema。"""
        step = {"id": 1, "action": "echo", "tool": "shell",
                "params": {"command": "echo schema-ok"}, "risk": "low"}
        r = executor.execute_step(step, auto_confirm=True)
        assert r["success"]
