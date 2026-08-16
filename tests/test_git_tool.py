"""GitTool 测试：status/diff/log/branch/add/commit/push。"""

import subprocess
import pytest
from tools.git import GitTool


@pytest.fixture
def git_repo(tmp_path):
    """创建一个临时 git 仓库，含初始提交。"""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=str(repo), capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=str(repo))
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=str(repo))

    (repo / "file.txt").write_text("hello\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=str(repo))
    subprocess.run(["git", "commit", "-m", "initial commit"], cwd=str(repo), capture_output=True)
    return repo


@pytest.fixture
def tool(git_repo):
    return GitTool(repo=str(git_repo))


class TestReadOnly:
    """只读操作。"""

    def test_status_clean(self, tool):
        r = tool.execute("status")
        assert r["success"]
        assert "工作区干净" in r["result"]

    def test_status_with_changes(self, tool, git_repo):
        (git_repo / "newfile.txt").write_text("new", encoding="utf-8")
        r = tool.execute("status")
        assert r["success"]
        assert "newfile.txt" in r["result"]

    def test_log(self, tool):
        r = tool.execute("log", count=3)
        assert r["success"]
        assert "initial commit" in r["result"]

    def test_branch(self, tool):
        r = tool.execute("branch")
        assert r["success"]
        assert r["result"].strip()  # 非空分支名

    def test_diff_empty(self, tool):
        r = tool.execute("diff")
        assert r["success"]
        assert "无改动" in r["result"]

    def test_diff_with_changes(self, tool, git_repo):
        (git_repo / "file.txt").write_text("changed\n", encoding="utf-8")
        r = tool.execute("diff")
        assert r["success"]
        assert "changed" in r["result"]


class TestWrite:
    """写操作。"""

    def test_add_and_commit(self, tool, git_repo):
        (git_repo / "new.txt").write_text("content", encoding="utf-8")

        r = tool.execute("add")
        assert r["success"]

        r = tool.execute("commit", message="test commit")
        assert r["success"]

        r = tool.execute("log", count=1)
        assert "test commit" in r["result"]

    def test_add_specific_file(self, tool, git_repo):
        (git_repo / "a.txt").write_text("a", encoding="utf-8")
        (git_repo / "b.txt").write_text("b", encoding="utf-8")

        r = tool.execute("add", files="a.txt")
        assert r["success"]

        r = tool.execute("diff", staged=True)
        assert "a.txt" in r["result"]
        assert "b.txt" not in r["result"]

    def test_commit_without_message(self, tool):
        r = tool.execute("commit", message="")
        assert not r["success"]
        assert "不能为空" in r["error"]

    def test_push_without_remote_fails(self, tool):
        """无远程仓库时 push 应失败并返回错误。"""
        r = tool.execute("push")
        assert not r["success"]


class TestErrors:
    """错误处理。"""

    def test_unknown_action(self, tool):
        r = tool.execute("rebase")
        assert not r["success"]
        assert "未知" in r["error"]

    def test_not_a_repo(self, tmp_path):
        """非 git 仓库目录。"""
        plain_dir = tmp_path / "plain"
        plain_dir.mkdir()
        t = GitTool(repo=str(plain_dir))
        r = t.execute("status")
        assert not r["success"]
