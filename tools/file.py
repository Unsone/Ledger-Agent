from pathlib import Path
from tools.base import Tool


class FileTool(Tool):
    """文件读写与精确编辑工具。

    与 shell 工具的区别：不需要拼接 shell 命令，直接精确操作文件内容。
    Planner 应优先用本工具做文件操作，而不是 echo > / python -c 重定向。
    """

    name = "file"
    description = (
        "读写和编辑任意文件。支持 read(带行号读取), write(创建/覆盖), "
        "edit(查找替换), append(追加), insert_after(行后插入), replace_line(替换某行)"
    )

    params_schema = {
        "read": [
            {"name": "path", "required": True, "desc": "文件路径"},
        ],
        "write": [
            {"name": "path", "required": True, "desc": "文件路径"},
            {"name": "content", "required": True, "desc": "完整文件内容"},
        ],
        "edit": [
            {"name": "path", "required": True, "desc": "文件路径"},
            {"name": "old_text", "required": True, "desc": "要查找的原文（必须唯一）"},
            {"name": "new_text", "required": True, "desc": "替换后的文本"},
        ],
        "append": [
            {"name": "path", "required": True, "desc": "文件路径"},
            {"name": "content", "required": True, "desc": "要追加的内容"},
        ],
        "insert_after": [
            {"name": "path", "required": True, "desc": "文件路径"},
            {"name": "line_number", "required": True, "desc": "行号（从1开始）"},
            {"name": "content", "required": True, "desc": "要插入的内容"},
        ],
        "replace_line": [
            {"name": "path", "required": True, "desc": "文件路径"},
            {"name": "line_number", "required": True, "desc": "行号（从1开始）"},
            {"name": "new_content", "required": True, "desc": "新的行内容"},
        ],
    }

    # 读取文件时最多返回的行数
    MAX_READ_LINES = 300

    def execute(self, action: str, path: str = None, filepath: str = None, **kwargs) -> dict:
        """统一入口。

        Args:
            action: read | write | edit | append | insert_after | replace_line
            path: 文件路径（绝对路径或相对路径）
            filepath: path 的别名（兼容 Planner 的不同命名）
            **kwargs: 各操作参数

        Returns:
            {"success": bool, "result": str, "error": str|None}
        """
        handlers = {
            "read": self._read,
            "write": self._write,
            "edit": self._edit,
            "append": self._append,
            "insert_after": self._insert_after,
            "replace_line": self._replace_line,
        }

        handler = handlers.get(action)
        if handler is None:
            return {
                "success": False,
                "result": None,
                "error": f"未知操作: '{action}'。支持: {', '.join(handlers.keys())}",
            }

        target = path or filepath
        if not target:
            return {"success": False, "result": None, "error": "缺少 'path' 参数"}

        try:
            full_path = Path(target).expanduser().resolve()
            return handler(full_path, **kwargs)
        except FileNotFoundError:
            return {"success": False, "result": None, "error": f"文件不存在: {target}"}
        except Exception as e:
            return {"success": False, "result": None, "error": str(e)}

    # ── 操作实现 ──────────────────────────────────────────

    def _read(self, path: Path, **kwargs) -> dict:
        """读取文件，带行号（方便后续 insert/replace 精确引用）。"""
        if not path.exists():
            return {"success": False, "result": None, "error": f"文件不存在: {path}"}
        if not path.is_file():
            return {"success": False, "result": None, "error": f"不是文件: {path}"}

        content = self._read_text(path)
        lines = content.split("\n")

        # 截断超长文件
        truncated = False
        if len(lines) > self.MAX_READ_LINES:
            lines = lines[:self.MAX_READ_LINES]
            truncated = True

        # 带行号输出，如 "  1 | import os"
        numbered = []
        width = len(str(len(lines)))
        for i, line in enumerate(lines, 1):
            numbered.append(f"{i:>{width}} | {line}")

        result = "\n".join(numbered)
        if truncated:
            result += f"\n... [文件共 {len(content.splitlines())} 行，只显示前 {self.MAX_READ_LINES} 行]"

        return {"success": True, "result": result, "error": None}

    def _write(self, path: Path, content: str, **kwargs) -> dict:
        """创建或覆盖文件。"""
        if not content:
            return {"success": False, "result": None, "error": "content 不能为空"}
        path.parent.mkdir(parents=True, exist_ok=True)
        self._write_text(path, content)
        return {"success": True, "result": f"已写入: {path}", "error": None}

    def _edit(self, path: Path, old_text: str, new_text: str, **kwargs) -> dict:
        """查找替换。old_text 必须唯一匹配，防止误改。"""
        if not path.exists():
            return {"success": False, "result": None, "error": f"文件不存在: {path}"}
        if not old_text:
            return {"success": False, "result": None, "error": "old_text 不能为空"}

        content = self._read_text(path)
        count = content.count(old_text)

        if count == 0:
            return {
                "success": False,
                "result": None,
                "error": f"未找到目标文本（可能已被修改），请先 read 确认当前内容",
            }
        if count > 1:
            return {
                "success": False,
                "result": None,
                "error": f"目标文本出现 {count} 次，不唯一。请提供更长的上下文使其唯一",
            }

        new_content = content.replace(old_text, new_text)
        self._write_text(path, new_content)
        return {"success": True, "result": f"已替换 1 处: {path}", "error": None}

    def _append(self, path: Path, content: str, **kwargs) -> dict:
        """在文件末尾追加。"""
        if not path.exists():
            return {"success": False, "result": None, "error": f"文件不存在: {path}（追加前请先 write 创建）"}
        with open(path, "a", encoding="utf-8") as f:
            f.write("\n" + content)
        return {"success": True, "result": f"已追加到: {path}", "error": None}

    def _insert_after(self, path: Path, line_number: int, content: str, **kwargs) -> dict:
        """在指定行号后插入内容。"""
        if not path.exists():
            return {"success": False, "result": None, "error": f"文件不存在: {path}"}

        lines = self._read_text(path).split("\n")
        if line_number < 0 or line_number > len(lines):
            return {
                "success": False,
                "result": None,
                "error": f"行号 {line_number} 超出范围（文件共 {len(lines)} 行）",
            }

        insert_lines = content.split("\n")
        new_lines = lines[:line_number] + insert_lines + lines[line_number:]
        self._write_text(path, "\n".join(new_lines))
        return {"success": True, "result": f"已在第 {line_number} 行后插入 {len(insert_lines)} 行", "error": None}

    def _replace_line(self, path: Path, line_number: int, new_content: str, **kwargs) -> dict:
        """替换指定行号的内容。"""
        if not path.exists():
            return {"success": False, "result": None, "error": f"文件不存在: {path}"}

        lines = self._read_text(path).split("\n")
        if line_number < 1 or line_number > len(lines):
            return {
                "success": False,
                "result": None,
                "error": f"行号 {line_number} 超出范围（文件共 {len(lines)} 行）",
            }

        lines[line_number - 1] = new_content
        self._write_text(path, "\n".join(lines))
        return {"success": True, "result": f"已替换第 {line_number} 行", "error": None}

    # ── 内部方法 ──────────────────────────────────────────

    @staticmethod
    def _read_text(path: Path) -> str:
        """读取文本文件，检测二进制并拒绝。

        统一内部换行为 LF：Windows 的 CRLF 读入后转为 LF，
        避免 edit 匹配和行号操作受换行符干扰。
        """
        raw = path.read_bytes()
        # 检测二进制（包含 null 字节）
        if b"\x00" in raw[:4096]:
            raise ValueError(f"看起来是二进制文件，拒绝读取: {path}")
        text = raw.decode("utf-8", errors="replace")
        return text.replace("\r\n", "\n")

    @staticmethod
    def _write_text(path: Path, content: str):
        """写入文本。newline='' 阻止 Windows 的 \\n→\\r\\n 自动翻译，
        保证写入内容与内存中一致（LF）。"""
        with open(path, "w", encoding="utf-8", newline="") as f:
            f.write(content)
