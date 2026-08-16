import subprocess
from pathlib import Path
from tools.base import Tool


class GitTool(Tool):
    """Git 版本控制工具：结构化封装常用 git 操作。

    与 shell 工具跑原始 git 命令的区别：输出结构化、参数类型化、
    不受 shell 注入影响（直接 subprocess 调 git 二进制）。
    """

    name = "git"
    description = (
        "Git 版本控制。支持 action: status(状态), diff(改动), log(历史), "
        "branch(当前分支), add(暂存), commit(提交), push(推送)"
    )

    params_schema = {
        "status": [],
        "diff": [
            {"name": "staged", "required": False, "desc": "True 时查看已暂存改动"},
        ],
        "log": [
            {"name": "count", "required": False, "desc": "显示最近 N 次提交"},
        ],
        "branch": [],
        "add": [
            {"name": "files", "required": False, "desc": "要暂存的文件，默认全部"},
        ],
        "commit": [
            {"name": "message", "required": True, "desc": "提交信息"},
        ],
        "push": [],
    }

    # 输出截断上限
    MAX_OUTPUT_LENGTH = 4000

    def __init__(self, repo: str = None):
        """repo: 仓库根目录，默认当前工作目录。"""
        self.repo = Path(repo) if repo else Path.cwd()

    def execute(self, action: str, **kwargs) -> dict:
        handlers = {
            "status": self._status,
            "diff": self._diff,
            "log": self._log,
            "branch": self._branch,
            "add": self._add,
            "commit": self._commit,
            "push": self._push,
        }

        handler = handlers.get(action)
        if handler is None:
            return {
                "success": False,
                "result": None,
                "error": f"未知操作: '{action}'。支持: {', '.join(handlers.keys())}",
            }

        try:
            return handler(**kwargs)
        except Exception as e:
            return {"success": False, "result": None, "error": str(e)}

    # ── 只读操作 ──────────────────────────────────────────

    def _status(self, **kwargs) -> dict:
        """git status --short：工作区状态。"""
        code, out, err = self._run("status", "--short")
        if code != 0:
            return {"success": False, "result": None, "error": err or "git status 失败"}
        return {"success": True, "result": out.strip() or "(工作区干净)", "error": None}

    def _diff(self, staged: bool = False, **kwargs) -> dict:
        """git diff：未暂存改动（staged=True 时看已暂存改动）。"""
        args = ["diff", "--staged"] if staged else ["diff"]
        code, out, err = self._run(*args)
        if code != 0:
            return {"success": False, "result": None, "error": err or "git diff 失败"}
        text = out.strip() or "(无改动)"
        return {"success": True, "result": self._truncate(text), "error": None}

    def _log(self, count: int = 5, **kwargs) -> dict:
        """git log：最近 N 次提交（oneline）。"""
        code, out, err = self._run("log", "--oneline", f"-{min(count, 50)}")
        if code != 0:
            return {"success": False, "result": None, "error": err or "git log 失败"}
        return {"success": True, "result": out.strip() or "(无提交记录)", "error": None}

    def _branch(self, **kwargs) -> dict:
        """git branch：当前分支名。"""
        code, out, err = self._run("branch", "--show-current")
        if code != 0:
            return {"success": False, "result": None, "error": err or "git branch 失败"}
        return {"success": True, "result": out.strip(), "error": None}

    # ── 写操作（Executor 安全层会拦截 confirm 级操作） ─────

    def _add(self, files: str = ".", **kwargs) -> dict:
        """git add：暂存文件（默认全部）。"""
        code, out, err = self._run("add", files)
        if code != 0:
            return {"success": False, "result": None, "error": err or "git add 失败"}
        return {"success": True, "result": f"已暂存: {files}", "error": None}

    def _commit(self, message: str, **kwargs) -> dict:
        """git commit：提交（需要 message）。"""
        if not message or not message.strip():
            return {"success": False, "result": None, "error": "commit message 不能为空"}
        code, out, err = self._run("commit", "-m", message)
        if code != 0:
            return {"success": False, "result": None, "error": err or "git commit 失败"}
        return {"success": True, "result": out.strip(), "error": None}

    def _push(self, **kwargs) -> dict:
        """git push：推送当前分支到远程。"""
        code, out, err = self._run("push")
        if code != 0:
            return {"success": False, "result": None, "error": err or "git push 失败"}
        return {"success": True, "result": out.strip() or "已推送", "error": None}

    # ── 内部方法 ──────────────────────────────────────────

    def _run(self, *args: str) -> tuple[int, str, str]:
        """运行 git 命令，返回 (returncode, stdout, stderr)。"""
        proc = subprocess.run(
            ["git", *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
            cwd=str(self.repo),
        )
        return proc.returncode, proc.stdout, proc.stderr

    def _truncate(self, text: str) -> str:
        if len(text) > self.MAX_OUTPUT_LENGTH:
            return text[:self.MAX_OUTPUT_LENGTH] + "\n... [diff 已截断]"
        return text
