from datetime import datetime
from pathlib import Path
from tools.base import Tool


class TaskInboxTool(Tool):
    """手动投喂入口：用户把消息/任务粘贴进笔记库的 Inbox.md，此工具读取并归档。"""

    name = "task_inbox"
    description = "读取笔记库 Inbox.md 中手动粘贴的任务，读取后可归档清空"

    params_schema = {
        "execute": [
            {"name": "archive", "required": False, "desc": "True 时读取后归档并清空 Inbox"},
        ],
    }

    def __init__(self, inbox_path: str = None):
        if inbox_path is None:
            inbox_path = Path(__file__).parent.parent / "obsidian" / "Inbox.md"
        self.inbox_path = Path(inbox_path)

    def execute(self, archive: bool = False, **kwargs) -> dict:
        """读取 Inbox.md 内容，可选归档后清空。

        Args:
            archive: 如果为 True，读取后将内容归档到 Inbox_archive.md 并清空 Inbox.md

        Returns:
            {"success": bool, "result": content, "error": None}
        """
        if not self.inbox_path.exists():
            return {"success": True, "result": "", "error": None}

        try:
            content = self.inbox_path.read_text(encoding="utf-8").strip()

            if archive and content:
                self._archive(content)
                self.inbox_path.write_text(
                    "<!-- 任务已归档 -->\n", encoding="utf-8"
                )

            return {"success": True, "result": content, "error": None}
        except Exception as e:
            return {"success": False, "result": None, "error": str(e)}

    def _archive(self, content: str):
        """将内容追加到 Inbox_archive.md，带时间戳。"""
        archive_path = self.inbox_path.parent / "Inbox_archive.md"
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
        entry = f"\n---\n## {timestamp}\n\n{content}\n"
        with open(archive_path, "a", encoding="utf-8") as f:
            f.write(entry)
