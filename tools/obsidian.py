import re
from datetime import datetime
from pathlib import Path
from tools.base import Tool


class ObsidianTool(Tool):
    """读写 Obsidian 笔记库：读取、创建、追加、列出、搜索笔记。"""

    name = "obsidian"
    description = (
        "读写 Obsidian 笔记库。支持操作: read(读取笔记), write(创建/覆盖), "
        "append(追加内容), list(列出目录), search(搜索内容), daily(写入今日日记)"
    )

    params_schema = {
        "read": [
            {"name": "path", "required": True, "desc": "笔记路径（vault 内相对路径）"},
        ],
        "write": [
            {"name": "path", "required": True, "desc": "笔记路径"},
            {"name": "content", "required": True, "desc": "markdown 内容"},
        ],
        "append": [
            {"name": "path", "required": True, "desc": "笔记路径"},
            {"name": "content", "required": True, "desc": "要追加的内容"},
        ],
        "list": [
            {"name": "path", "required": False, "desc": "目录路径，空为 vault 根"},
        ],
        "search": [
            {"name": "query", "required": True, "desc": "搜索关键词"},
            {"name": "path", "required": False, "desc": "限定搜索目录"},
        ],
        "daily": [
            {"name": "content", "required": True, "desc": "要写入今日日记的内容"},
        ],
    }

    def __init__(self, vault_path: str = None):
        if vault_path is None:
            vault_path = Path(__file__).parent.parent / "obsidian"
        self.vault_path = Path(vault_path).resolve()

    def execute(self, action: str = None, operation: str = None, **kwargs) -> dict:
        """统一入口，按 action/operation 分发到具体方法。

        Args:
            action: 操作类型 — read | write | append | list | search | daily
            operation: action 的别名（兼容 Planner 的不同命名）
            **kwargs: 各操作的参数

        Returns:
            {"success": bool, "result": str, "error": str|None}
        """
        # 兼容 Planner 可能使用的不同参数名
        op = action or operation

        handlers = {
            "read": self._read,
            "write": self._write,
            "append": self._append,
            "list": self._list,
            "search": self._search,
            "daily": self._daily,
        }

        if not op:
            return {
                "success": False,
                "result": None,
                "error": "缺少 'action' 参数。支持: read, write, append, list, search, daily",
            }

        handler = handlers.get(op)
        if handler is None:
            return {
                "success": False,
                "result": None,
                "error": f"未知操作: '{op}'。支持: {', '.join(handlers.keys())}",
            }

        try:
            return handler(**kwargs)
        except Exception as e:
            return {"success": False, "result": None, "error": str(e)}

    # ── 操作实现 ──────────────────────────────────────────

    def _read(self, path: str, **kwargs) -> dict:
        """读取笔记全文。

        Args:
            path: 笔记路径，相对于 vault 根目录，如 "Daily/2026-08-10.md"
        """
        full_path = self._resolve(path)
        if not full_path.exists():
            return {"success": False, "result": None, "error": f"文件不存在: {path}"}
        if not full_path.is_file():
            return {"success": False, "result": None, "error": f"不是文件: {path}"}

        content = full_path.read_text(encoding="utf-8")
        return {"success": True, "result": content, "error": None}

    def _write(self, path: str, content: str, **kwargs) -> dict:
        """创建或覆盖笔记。

        Args:
            path: 笔记路径（相对于 vault）
            content: 要写入的内容（markdown）
        """
        full_path = self._resolve(path)
        full_path.parent.mkdir(parents=True, exist_ok=True)
        full_path.write_text(content, encoding="utf-8")
        return {"success": True, "result": f"已写入: {path}", "error": None}

    def _append(self, path: str, content: str, **kwargs) -> dict:
        """在笔记末尾追加内容。

        Args:
            path: 笔记路径（相对于 vault）
            content: 要追加的内容
        """
        full_path = self._resolve(path)
        if not full_path.exists():
            # 文件不存在则创建
            full_path.parent.mkdir(parents=True, exist_ok=True)
            full_path.write_text(content, encoding="utf-8")
        else:
            with open(full_path, "a", encoding="utf-8") as f:
                f.write("\n" + content)

        return {"success": True, "result": f"已追加到: {path}", "error": None}

    def _list(self, path: str = "", **kwargs) -> dict:
        """列出目录内容。

        Args:
            path: 目录路径（相对于 vault），空字符串表示 vault 根目录
        """
        full_path = self._resolve(path) if path else self.vault_path
        if not full_path.exists():
            return {"success": False, "result": None, "error": f"目录不存在: {path}"}
        if not full_path.is_dir():
            return {"success": False, "result": None, "error": f"不是目录: {path}"}

        items = []
        for item in sorted(full_path.iterdir()):
            prefix = "📁" if item.is_dir() else "📄"
            items.append(f"{prefix} {item.name}")

        result = "\n".join(items) if items else "(空目录)"
        return {"success": True, "result": result, "error": None}

    def _search(self, query: str, path: str = "", **kwargs) -> dict:
        """在笔记中搜索文本（简单子串匹配）。

        Args:
            query: 搜索关键词
            path: 限制搜索范围（目录，相对于 vault），空字符串表示全部
        """
        if not query:
            return {"success": False, "result": None, "error": "搜索关键词不能为空"}

        search_root = self._resolve(path) if path else self.vault_path
        if not search_root.exists():
            return {"success": False, "result": None, "error": f"目录不存在: {path}"}

        results = []
        pattern = re.compile(re.escape(query), re.IGNORECASE)

        # 遍历 markdown 文件
        md_files = search_root.rglob("*.md") if search_root.is_dir() else [search_root]
        for md_file in md_files:
            if not md_file.is_file():
                continue
            try:
                for lineno, line in enumerate(md_file.read_text(encoding="utf-8").splitlines(), 1):
                    if pattern.search(line):
                        rel_path = md_file.relative_to(self.vault_path)
                        results.append(f"{rel_path}:{lineno}: {line.strip()[:100]}")
            except Exception:
                continue

        if not results:
            return {"success": True, "result": "未找到匹配结果。", "error": None}

        # 最多返回 50 条
        result = "\n".join(results[:50])
        if len(results) > 50:
            result += f"\n... 还有 {len(results) - 50} 条结果未显示"
        return {"success": True, "result": result, "error": None}

    def _daily(self, content: str, **kwargs) -> dict:
        """将内容追加到今日日记 obsidian/Daily/YYYY-MM-DD.md。

        Args:
            content: 要追加的内容
        """
        today = datetime.now().strftime("%Y-%m-%d")
        path = f"Daily/{today}.md"
        timestamp = datetime.now().strftime("%H:%M")
        entry = f"\n## {timestamp}\n\n{content}\n"
        return self._append(path, entry)

    # ── 内部方法 ──────────────────────────────────────────

    def _resolve(self, path: str) -> Path:
        """将相对路径解析为 vault 内的绝对路径，防止路径穿越。"""
        # 规范化路径
        normalized = Path(path)
        # 去掉开头的 / 或 ./
        if normalized.is_absolute() or str(normalized).startswith(".."):
            raise ValueError(f"不允许的路径（必须是 vault 内相对路径）: {path}")

        resolved = (self.vault_path / normalized).resolve()

        # 二次确认没有穿越到 vault 外面（is_relative_to 比 startswith 严格，
        # 防止 obsidian-evil 这种兄弟目录绕过前缀检查）
        try:
            resolved.relative_to(self.vault_path)
        except ValueError:
            raise ValueError(f"路径穿越检测: {path}")

        return resolved
