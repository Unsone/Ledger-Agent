import subprocess
import os
import sys
from pathlib import Path
from tools.base import Tool


class ShellTool(Tool):
    name = "shell"
    description = "执行命令行指令（Windows/Linux），受 config/safety.yaml 规则约束"

    # 输出超过此长度时截断，避免 LLM 上下文溢出
    MAX_OUTPUT_LENGTH = 4000

    def execute(self, command: str, cwd: str = None, **kwargs) -> dict:
        """执行 shell 命令。

        Args:
            command: 要执行的命令
            cwd: 工作目录（可选），默认为当前目录

        Returns:
            {"success": bool, "result": stdout, "error": stderr|None}
        """
        # 构建环境变量：强制 UTF-8，避免 Windows GBK 乱码
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        env["LANG"] = "en_US.UTF-8"
        env["LC_ALL"] = "en_US.UTF-8"

        # 预展开 $HOME / %USERPROFILE%，因为 Windows cmd 不识别 $HOME
        command = self._expand_env_vars(command)

        # Windows cmd 需要先切到 UTF-8 代码页
        if sys.platform == "win32":
            command = f"chcp 65001 > nul 2>&1 && {command}"

        try:
            proc = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=120,
                cwd=cwd or None,
                env=env,
            )

            stdout = proc.stdout or ""
            stderr = proc.stderr or None

            # 截断过长输出
            if len(stdout) > self.MAX_OUTPUT_LENGTH:
                stdout = stdout[:self.MAX_OUTPUT_LENGTH] + "\n... [输出已截断]"
            if stderr and len(stderr) > self.MAX_OUTPUT_LENGTH:
                stderr = stderr[:self.MAX_OUTPUT_LENGTH] + "\n... [输出已截断]"

            return {
                "success": proc.returncode == 0,
                "result": stdout,
                "error": stderr,
            }
        except subprocess.TimeoutExpired:
            return {
                "success": False,
                "result": None,
                "error": "命令执行超时（120秒）",
            }
        except Exception as e:
            return {"success": False, "result": None, "error": str(e)}

    @staticmethod
    def _expand_env_vars(command: str) -> str:
        """预展开常见的环境变量，兼容 Windows cmd 不认识 $HOME 的问题。

        Planner 可能使用 $HOME 或 %USERPROFILE% 来引用用户目录，
        Windows cmd.exe 只认 %VAR% 格式，bash 只认 $VAR。
        这里统一做预展开，不管哪种格式都能正确替换。

        重要：展开后将反斜杠转为正斜杠，避免 C:\\Users\\... 中的
        \\U、\\N 等被 Python 字符串当作 Unicode/转义序列。
        """
        home = os.environ.get("HOME", "") or os.environ.get("USERPROFILE", "")
        userprofile = os.environ.get("USERPROFILE", "") or home

        replacements = {
            "$HOME": home,
            "${HOME}": home,
            "%HOME%": home,
            "%USERPROFILE%": userprofile,
            "$USERPROFILE": userprofile,
            "${USERPROFILE}": userprofile,
        }

        for var, value in replacements.items():
            if value:
                # Windows 路径反斜杠转正斜杠，避免 Python 字符串转义问题
                safe_value = value.replace("\\", "/")
                command = command.replace(var, safe_value)

        return command
