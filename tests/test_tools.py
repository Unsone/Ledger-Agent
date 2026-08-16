"""工具层测试：ShellTool, ObsidianTool, TaskInboxTool, WebSearchTool。"""

import pytest
from pathlib import Path
from tools.shell import ShellTool
from tools.obsidian import ObsidianTool
from tools.task_inbox import TaskInboxTool
from tools.base import Tool


class TestToolBase:
    """Tool 抽象基类规范。"""

    def test_tool_is_abstract(self):
        """Tool 不能直接实例化。"""
        with pytest.raises(TypeError):
            Tool()  # type: ignore

    def test_subclass_has_name(self):
        """子类必须有 name 和 description。"""
        assert ShellTool.name == "shell"
        assert "shell" in ShellTool.description.lower() or "命令" in ShellTool.description

    def test_all_tools_declare_params_schema(self):
        """所有已注册工具都应声明 params_schema（含必填参数标记）。"""
        from tools.file import FileTool
        from tools.git import GitTool
        from tools.python_runner import PythonRunnerTool
        from tools.web_search import WebSearchTool

        tools = [ShellTool(), FileTool(), GitTool(), PythonRunnerTool(),
                 ObsidianTool(), WebSearchTool(), TaskInboxTool()]
        for t in tools:
            assert t.params_schema, f"{t.name} 缺少 params_schema"
            for action, params in t.params_schema.items():
                for p in params:
                    assert "name" in p and "required" in p, \
                        f"{t.name}.{action} 的参数定义缺少 name/required 字段"


class TestShellTool:
    """ShellTool 命令行执行测试。"""

    def test_simple_echo(self):
        """基本命令执行。"""
        tool = ShellTool()
        r = tool.execute("echo hello-test")
        assert r["success"]
        assert "hello-test" in r["result"]

    def test_failed_command(self):
        """执行不存在的命令。"""
        tool = ShellTool()
        r = tool.execute("this_command_does_not_exist_xyz")
        assert not r["success"]
        assert r["error"] is not None

    def test_cwd_parameter(self):
        """指定工作目录。"""
        tool = ShellTool()
        r = tool.execute("dir /b", cwd="D:/UV/Personal-Agent/agent")
        assert r["success"]
        # agent 目录下应该有 llm.py
        assert "llm.py" in r["result"]

    def test_home_expansion(self):
        """$HOME 环境变量展开。"""
        tool = ShellTool()
        expanded = tool._expand_env_vars("echo $HOME/Downloads")
        assert "$HOME" not in expanded
        assert "Downloads" in expanded or "Downloads" in expanded

    def test_userprofile_expansion(self):
        """%USERPROFILE% 环境变量展开。"""
        tool = ShellTool()
        expanded = tool._expand_env_vars("dir %USERPROFILE%")
        assert "%USERPROFILE%" not in expanded

    def test_backslash_to_slash(self):
        """展开后路径中不应有反斜杠（防止 Python 转义问题）。"""
        tool = ShellTool()
        import os
        if "\\" in os.environ.get("HOME", ""):
            expanded = tool._expand_env_vars("python -c '$HOME'")
            # 展开后的值中不应有反斜杠
            assert "\\\\" not in expanded  # 双反斜杠不应出现

    def test_output_truncation(self):
        """长输出应被截断。"""
        tool = ShellTool()
        # 生成超过 MAX_OUTPUT_LENGTH 的输出
        r = tool.execute("python -c \"print('x' * 5000)\"")
        assert r["success"]
        assert "... [输出已截断]" in r["result"]


class TestObsidianTool:
    """ObsidianTool vault 读写测试。"""

    def test_write_and_read(self, temp_vault):
        """写入后读取应一致。"""
        tool = ObsidianTool(vault_path=str(temp_vault))
        r = tool.execute("write", path="test.md", content="# 标题\n\n内容")
        assert r["success"]

        r = tool.execute("read", path="test.md")
        assert r["success"]
        assert "# 标题" in r["result"]
        assert "内容" in r["result"]

    def test_append(self, temp_vault):
        """追加内容。"""
        tool = ObsidianTool(vault_path=str(temp_vault))
        tool.execute("write", path="note.md", content="第一行")
        tool.execute("append", path="note.md", content="第二行")

        r = tool.execute("read", path="note.md")
        assert "第一行" in r["result"]
        assert "第二行" in r["result"]

    def test_list_directory(self, temp_vault):
        """列出目录内容。"""
        tool = ObsidianTool(vault_path=str(temp_vault))
        tool.execute("write", path="test1.md", content="a")
        tool.execute("write", path="test2.md", content="b")

        r = tool.execute("list")
        assert r["success"]
        assert "test1.md" in r["result"] or "test2.md" in r["result"]
        assert "Daily" in r["result"]  # 预设目录

    def test_search(self, temp_vault):
        """内容搜索。"""
        tool = ObsidianTool(vault_path=str(temp_vault))
        tool.execute("write", path="a.md", content="hello world")
        tool.execute("write", path="b.md", content="goodbye")

        r = tool.execute("search", query="hello")
        assert r["success"]
        assert "a.md" in r["result"]
        assert "b.md" not in r["result"]

    def test_search_no_results(self, temp_vault):
        """搜索无结果。"""
        tool = ObsidianTool(vault_path=str(temp_vault))
        r = tool.execute("search", query="zzz_nonexistent_zzz")
        assert r["success"]
        assert "未找到" in r["result"]

    def test_read_nonexistent(self, temp_vault):
        """读取不存在的文件。"""
        tool = ObsidianTool(vault_path=str(temp_vault))
        r = tool.execute("read", path="nonexistent.md")
        assert not r["success"]
        assert "不存在" in r["error"]

    def test_path_traversal_blocked(self, temp_vault):
        """路径穿越应被拦截。"""
        tool = ObsidianTool(vault_path=str(temp_vault))
        r = tool.execute("read", path="../../secret.txt")
        assert not r["success"]
        error_text = r.get("error", "")
        assert "不允许" in error_text or "穿越" in error_text

    def test_sibling_directory_bypass(self, temp_vault):
        """兄弟目录不能绕过 is_relative_to 检查（regression test）。"""
        vault = temp_vault
        sibling = vault.parent / "vault-evil"
        sibling.mkdir()
        (sibling / "evil.md").write_text("bad", encoding="utf-8")

        tool = ObsidianTool(vault_path=str(vault))
        # 尝试用 .. 访问兄弟目录
        r = tool.execute("read", path="../vault-evil/evil.md")
        assert not r["success"]

    def test_daily_note(self, temp_vault):
        """Daily 日记写入。"""
        tool = ObsidianTool(vault_path=str(temp_vault))
        r = tool.execute("daily", content="测试日记内容")
        assert r["success"]

        from datetime import datetime
        today = datetime.now().strftime("%Y-%m-%d")
        r = tool.execute("read", path=f"Daily/{today}.md")
        assert r["success"]
        assert "测试日记内容" in r["result"]

    def test_unknown_action(self, temp_vault):
        """未知操作应返回错误。"""
        tool = ObsidianTool(vault_path=str(temp_vault))
        r = tool.execute("delete", path="x.md")
        assert not r["success"]
        assert "未知" in r["error"]

    def test_operation_alias(self, temp_vault):
        """operation 和 action 参数应等效。"""
        tool = ObsidianTool(vault_path=str(temp_vault))
        r1 = tool.execute(action="read", path="x.md")
        assert not r1["success"]  # 文件不存在，但参数识别正确

        r2 = tool.execute(operation="read", path="x.md")
        assert not r2["success"]  # 同样识别正确
        # 两者的错误应该一样（文件不存在，不是参数错误）
        assert "不存在" in r1["error"]
        assert "不存在" in r2["error"]


class TestTaskInboxTool:
    """TaskInboxTool 任务收件箱测试。"""

    def test_read_inbox(self, tmp_path):
        """读取 Inbox 内容。"""
        inbox = tmp_path / "Inbox.md"
        inbox.write_text("任务1: 测试\n任务2: 部署", encoding="utf-8")

        tool = TaskInboxTool(inbox_path=str(inbox))
        r = tool.execute()
        assert r["success"]
        assert "任务1" in r["result"]
        assert "任务2" in r["result"]

    def test_read_empty_inbox(self, tmp_path):
        """读取不存在的 Inbox。"""
        tool = TaskInboxTool(inbox_path=str(tmp_path / "nonexistent.md"))
        r = tool.execute()
        assert r["success"]
        assert r["result"] == ""

    def test_archive(self, tmp_path):
        """归档后 Inbox 应清空。"""
        inbox = tmp_path / "Inbox.md"
        inbox.write_text("归档测试内容", encoding="utf-8")

        tool = TaskInboxTool(inbox_path=str(inbox))
        r = tool.execute(archive=True)
        assert r["success"]
        assert "归档测试内容" in r["result"]

        # Inbox 应清空
        after = inbox.read_text(encoding="utf-8")
        assert "归档测试内容" not in after
