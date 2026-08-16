import subprocess
import sys
import tempfile
import time
from pathlib import Path
from tools.base import Tool


class PythonRunnerTool(Tool):
    """Python 代码执行工具：在隔离子进程中运行代码，捕获完整执行信息。

    与 shell 工具跑 `python xxx.py` 的区别：
    - 结构化的执行结果（stdout / stderr / 退出码 / 耗时）
    - 超时与输出上限，防死循环和输出洪泛拖死 Agent
    - 崩溃不影响主进程（子进程隔离）
    - 内联代码自动落盘到临时目录，无引号转义问题
    """

    name = "python_runner"
    description = (
        "执行 Python 代码。支持 code(内联代码片段) 或 script(脚本文件路径)，"
        "返回 stdout/stderr/退出码/耗时。用于运行测试、验证脚本、执行代码片段"
    )

    params_schema = {
        "run": [
            {"name": "code", "required": False, "desc": "内联代码字符串（与 script 二选一）"},
            {"name": "script", "required": False, "desc": "脚本文件路径（与 code 二选一）"},
            {"name": "cwd", "required": False, "desc": "工作目录"},
            {"name": "timeout", "required": False, "desc": "超时秒数，默认 60，最大 300"},
        ],
    }

    # 资源限制（"资源沙箱"：防死循环/输出洪泛，不防恶意代码）
    DEFAULT_TIMEOUT = 60   # 秒
    MAX_TIMEOUT = 300      # 秒
    MAX_OUTPUT = 4000      # stdout/stderr 各截断到此长度

    def execute(self, action: str = "run", code: str = None, script: str = None,
                cwd: str = None, timeout: int = None, **kwargs) -> dict:
        """执行 Python 代码。

        Args:
            action: 目前只支持 "run"
            code: 内联代码字符串（与 script 二选一）
            script: 脚本文件路径（与 code 二选一）
            cwd: 工作目录
            timeout: 超时秒数（默认 60，最大 300）

        Returns:
            {
                "success": bool,       # 退出码 0 且未超时
                "result": str,         # stdout（截断）
                "error": str|None,     # stderr / traceback / 超时信息
            }
        """
        if action != "run":
            return {"success": False, "result": None, "error": f"未知操作: '{action}'。只支持 run"}

        if not code and not script:
            return {"success": False, "result": None, "error": "code 和 script 必须提供其一"}

        timeout = min(timeout or self.DEFAULT_TIMEOUT, self.MAX_TIMEOUT)

        try:
            # 内联代码 → 写入临时目录执行（干净工作目录 + 无引号转义）
            tmp_dir = None
            if code:
                tmp_dir = tempfile.mkdtemp(prefix="pa_runner_")
                script_path = Path(tmp_dir) / "run_script.py"
                script_path.write_text(code, encoding="utf-8")
            else:
                script_path = Path(script).resolve()
                if not script_path.exists():
                    return {"success": False, "result": None, "error": f"脚本不存在: {script}"}

            start = time.monotonic()
            proc = subprocess.run(
                [sys.executable, str(script_path)],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                cwd=cwd or None,
            )
            duration = time.monotonic() - start

            stdout = self._truncate(proc.stdout or "")
            stderr = self._truncate(proc.stderr or "")

            success = proc.returncode == 0

            # 组装 result：stdout 为主，成功时附带 stderr 警告（如 DeprecationWarning）
            result_parts = []
            if stdout:
                result_parts.append(f"--- stdout ---\n{stdout}")
            if success and stderr:
                result_parts.append(f"--- stderr(警告) ---\n{stderr}")
            if success:
                result_parts.append(f"[退出码 0，耗时 {duration:.2f}s]")
            result = "\n".join(result_parts) if result_parts else "(无输出)"

            # error 只在失败时使用：含退出码和 traceback
            if success:
                error = None
            else:
                error = (
                    f"[退出码 {proc.returncode}，耗时 {duration:.2f}s]\n"
                    f"--- stderr ---\n{stderr}" if stderr else
                    f"[退出码 {proc.returncode}，耗时 {duration:.2f}s]（无 stderr 输出）"
                )

            return {"success": success, "result": result, "error": error}

        except subprocess.TimeoutExpired:
            return {
                "success": False,
                "result": None,
                "error": f"执行超时（{timeout}s）——可能存在死循环或阻塞调用",
            }
        except Exception as e:
            return {"success": False, "result": None, "error": str(e)}
        finally:
            if tmp_dir:
                import shutil
                shutil.rmtree(tmp_dir, ignore_errors=True)

    def _truncate(self, text: str) -> str:
        if len(text) > self.MAX_OUTPUT:
            return text[:self.MAX_OUTPUT] + f"\n... [已截断，共 {len(text)} 字符]"
        return text
