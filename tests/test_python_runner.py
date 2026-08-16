"""PythonRunnerTool 测试：内联代码、脚本、超时、输出截断、错误捕获。"""

import pytest
from tools.python_runner import PythonRunnerTool


@pytest.fixture
def tool():
    return PythonRunnerTool()


class TestRunInlineCode:
    """内联代码执行。"""

    def test_simple_code(self, tool):
        r = tool.execute("run", code="print('hello runner')")
        assert r["success"]
        assert "hello runner" in r["result"]

    def test_stdout_captured(self, tool):
        r = tool.execute("run", code="for i in range(3):\n    print(f'line {i}')")
        assert r["success"]
        assert "line 0" in r["result"]
        assert "line 2" in r["result"]

    def test_syntax_error(self, tool):
        """语法错误 → success=False，error 含 traceback。"""
        r = tool.execute("run", code="def broken(:\n    pass")
        assert not r["success"]
        assert "SyntaxError" in r["error"]

    def test_runtime_exception(self, tool):
        """运行时异常 → 返回 traceback。"""
        r = tool.execute("run", code="x = 1 / 0")
        assert not r["success"]
        assert "ZeroDivisionError" in r["error"]

    def test_exit_code_in_error(self, tool):
        """失败时 error 含退出码。"""
        r = tool.execute("run", code="import sys; sys.exit(42)")
        assert not r["success"]
        assert "42" in r["error"]

    def test_multiline_with_comments(self, tool):
        """多行代码含注释。"""
        r = tool.execute("run", code="# 注释\nresult = 1 + 1\nprint(result)")
        assert r["success"]
        assert "2" in r["result"]

    def test_stderr_vs_stdout(self, tool):
        """成功时 stderr 警告应保留在 result 中，error 为 None。"""
        r = tool.execute("run", code="import sys; sys.stderr.write('warning!')")
        assert r["success"]
        assert "warning!" in r["result"]
        assert r["error"] is None


class TestRunScript:
    """脚本文件执行。"""

    def test_script_file(self, tool, tmp_path):
        script = tmp_path / "test_script.py"
        script.write_text("print('from script')", encoding="utf-8")
        r = tool.execute("run", script=str(script))
        assert r["success"]
        assert "from script" in r["result"]

    def test_nonexistent_script(self, tool):
        r = tool.execute("run", script="D:/nonexistent/script.py")
        assert not r["success"]
        assert "不存在" in r["error"]

    def test_cwd_parameter(self, tool, tmp_path):
        """cwd 影响相对路径解析。"""
        workdir = tmp_path / "work"
        workdir.mkdir()
        (workdir / "data.txt").write_text("42", encoding="utf-8")
        r = tool.execute(
            "run",
            code="with open('data.txt') as f:\n    print(f.read())",
            cwd=str(workdir),
        )
        assert r["success"]
        assert "42" in r["result"]


class TestResourceLimits:
    """资源限制。"""

    def test_timeout_infinite_loop(self, tool):
        """死循环应被超时终止。"""
        r = tool.execute("run", code="while True:\n    pass", timeout=3)
        assert not r["success"]
        assert "超时" in r["error"]

    def test_output_truncation(self, tool):
        """长输出应被截断。"""
        r = tool.execute("run", code="print('x' * 10000)")
        assert r["success"]
        assert "已截断" in r["result"]

    def test_timeout_capped(self, tool):
        """timeout 超过 MAX_TIMEOUT 时应被限制。"""
        r = tool.execute("run", code="print(1)", timeout=99999)
        assert r["success"]  # 正常执行
        # 无法直接验证内部 cap，但确认不报错即可


class TestInputValidation:
    """参数校验。"""

    def test_no_code_or_script(self, tool):
        r = tool.execute("run")
        assert not r["success"]
        assert "code 和 script" in r["error"]

    def test_unknown_action(self, tool):
        r = tool.execute("compile", code="print(1)")
        assert not r["success"]
        assert "未知" in r["error"]


class TestIsolation:
    """子进程隔离。"""

    def test_crash_does_not_kill_tool(self, tool):
        """脚本崩溃后工具仍可继续使用。"""
        r1 = tool.execute("run", code="import os; os._exit(1)")
        assert not r1["success"]

        # 工具实例仍可用
        r2 = tool.execute("run", code="print('still alive')")
        assert r2["success"]
        assert "still alive" in r2["result"]

    def test_temp_dir_cleaned(self, tool, tmp_path):
        """内联代码执行后临时目录应被清理。"""
        import glob
        before = set(glob.glob(str(tmp_path / "..") + "/pa_runner_*"))
        tool.execute("run", code="print('x')")
        after = set(glob.glob(str(tmp_path / "..") + "/pa_runner_*"))
        assert before == after  # 没有新增残留
