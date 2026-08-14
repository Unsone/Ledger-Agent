"""FileTool 测试：读取（带行号）、写入、编辑、插入、替换行。"""

import pytest
from pathlib import Path
from tools.file import FileTool


@pytest.fixture
def tool():
    return FileTool()


@pytest.fixture
def sample_file(tmp_path):
    """创建一个样例文件。"""
    f = tmp_path / "sample.py"
    f.write_text(
        "import os\n\ndef hello():\n    print('hello')\n\ndef main():\n    hello()\n",
        encoding="utf-8",
    )
    return f


class TestRead:
    """读取操作。"""

    def test_read_with_line_numbers(self, tool, sample_file):
        """读取应带行号。"""
        r = tool.execute("read", path=str(sample_file))
        assert r["success"]
        # 第 1 行应显示 "1 | import os"
        assert "1 | import os" in r["result"]

    def test_read_nonexistent(self, tool, tmp_path):
        """读取不存在的文件。"""
        r = tool.execute("read", path=str(tmp_path / "nope.py"))
        assert not r["success"]
        assert "不存在" in r["error"]

    def test_read_binary_rejected(self, tool, tmp_path):
        """二进制文件应被拒绝。"""
        bin_file = tmp_path / "data.bin"
        bin_file.write_bytes(b"\x00\x01\x02\x00binary")
        r = tool.execute("read", path=str(bin_file))
        assert not r["success"]
        assert "二进制" in r["error"]


class TestWrite:
    """写入操作。"""

    def test_write_new_file(self, tool, tmp_path):
        """创建新文件。"""
        target = tmp_path / "new.txt"
        r = tool.execute("write", path=str(target), content="hello file tool")
        assert r["success"]
        assert target.read_text(encoding="utf-8") == "hello file tool"

    def test_write_overwrites(self, tool, sample_file):
        """覆盖已有文件。"""
        r = tool.execute("write", path=str(sample_file), content="完全不同的内容")
        assert r["success"]
        assert sample_file.read_text(encoding="utf-8") == "完全不同的内容"

    def test_write_empty_content_rejected(self, tool, tmp_path):
        """空内容应被拒绝。"""
        r = tool.execute("write", path=str(tmp_path / "x.txt"), content="")
        assert not r["success"]


class TestEdit:
    """查找替换。"""

    def test_edit_unique_match(self, tool, sample_file):
        """唯一匹配的替换。"""
        r = tool.execute("edit", path=str(sample_file), old_text="print('hello')", new_text="print('hi')")
        assert r["success"]
        content = sample_file.read_text(encoding="utf-8")
        assert "print('hi')" in content
        assert "print('hello')" not in content

    def test_edit_not_found(self, tool, sample_file):
        """目标不存在时报错。"""
        r = tool.execute("edit", path=str(sample_file), old_text="def nonexistent():", new_text="x")
        assert not r["success"]
        assert "未找到" in r["error"]

    def test_edit_ambiguous(self, tool, sample_file):
        """目标出现多次时报错。"""
        # "hello" 在文件中出现 2 次（def hello 和 print('hello')）
        r = tool.execute("edit", path=str(sample_file), old_text="hello", new_text="world")
        assert not r["success"]
        assert "不唯一" in r["error"]

    def test_edit_multiline(self, tool, sample_file):
        """多行替换。"""
        r = tool.execute(
            "edit",
            path=str(sample_file),
            old_text="def hello():\n    print('hello')",
            new_text="def hello():\n    return 'hello'",
        )
        assert r["success"]
        assert "return 'hello'" in sample_file.read_text(encoding="utf-8")


class TestAppend:
    """追加。"""

    def test_append(self, tool, sample_file):
        """追加到末尾。"""
        r = tool.execute("append", path=str(sample_file), content="# 注释")
        assert r["success"]
        assert sample_file.read_text(encoding="utf-8").endswith("# 注释")

    def test_append_nonexistent(self, tool, tmp_path):
        """追加到不存在的文件应报错。"""
        r = tool.execute("append", path=str(tmp_path / "nope.txt"), content="x")
        assert not r["success"]


class TestInsertAfter:
    """行后插入。"""

    def test_insert_after_valid_line(self, tool, sample_file):
        """在第 1 行后插入。"""
        r = tool.execute("insert_after", path=str(sample_file), line_number=1, content="# 新注释")
        assert r["success"]
        lines = sample_file.read_text(encoding="utf-8").split("\n")
        assert lines[0] == "import os"
        assert lines[1] == "# 新注释"

    def test_insert_after_out_of_range(self, tool, sample_file):
        """行号超出范围。"""
        r = tool.execute("insert_after", path=str(sample_file), line_number=999, content="x")
        assert not r["success"]
        assert "超出范围" in r["error"]


class TestReplaceLine:
    """替换指定行。"""

    def test_replace_line(self, tool, sample_file):
        """替换第 1 行。"""
        r = tool.execute("replace_line", path=str(sample_file), line_number=1, new_content="import sys")
        assert r["success"]
        lines = sample_file.read_text(encoding="utf-8").split("\n")
        assert lines[0] == "import sys"

    def test_replace_line_out_of_range(self, tool, sample_file):
        """行号超出范围。"""
        r = tool.execute("replace_line", path=str(sample_file), line_number=0, new_content="x")
        assert not r["success"]
        assert "超出范围" in r["error"]


class TestUnknownAction:
    """未知操作。"""

    def test_unknown_action(self, tool, tmp_path):
        r = tool.execute("delete", path=str(tmp_path / "x.txt"))
        assert not r["success"]
        assert "未知" in r["error"]

    def test_missing_path(self, tool):
        """缺少 path 参数应报错。"""
        r = tool.execute("read")
        assert not r["success"]
        assert "path" in r["error"]

    def test_filepath_alias(self, tool, tmp_path):
        """filepath 是 path 的别名（Planner 可能用不同命名）。"""
        target = tmp_path / "alias.txt"
        r = tool.execute("write", filepath=str(target), content="alias works")
        assert r["success"]
        assert target.read_text(encoding="utf-8") == "alias works"
