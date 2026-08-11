from datetime import datetime
from pathlib import Path
import os
import tempfile


class Memory:
    """用户记忆系统：管理用户画像、项目状态，每次写前自动存快照。

    Phase 4：提供上下文给 Agent，让 LLM 了解"你是谁、在做什么项目"。
    后续 Phase 会接入自动提炼（从 Daily 总结）和向量检索。
    """

    def __init__(self, memory_dir: str = None):
        if memory_dir is None:
            memory_dir = Path(__file__).parent.parent / "memory"
        self.memory_dir = Path(memory_dir)
        self.history_dir = self.memory_dir / "history"
        self.history_dir.mkdir(parents=True, exist_ok=True)

        self.profile_path = self.memory_dir / "profile.md"
        self.projects_path = self.memory_dir / "projects.md"

        # 确保文件存在
        self._ensure_file(self.profile_path, self._default_profile())
        self._ensure_file(self.projects_path, self._default_projects())

    # ── 读取 ─────────────────────────────────────────────

    def get_profile(self) -> str:
        """读取用户画像全文。"""
        return self.profile_path.read_text(encoding="utf-8")

    def get_projects(self) -> str:
        """读取项目状态全文。"""
        return self.projects_path.read_text(encoding="utf-8")

    def get_context(self) -> str:
        """获取完整记忆上下文，用于注入 LLM 对话。

        格式为 markdown，包含用户画像和项目状态两部分。
        若内容仅剩空模板则返回空字符串。
        """
        parts = []
        profile = self.get_profile().strip()
        projects = self.get_projects().strip()

        if profile and profile != self._default_profile().strip():
            parts.append(f"## 用户画像\n\n{profile}")

        if projects and projects != self._default_projects().strip():
            parts.append(f"## 项目状态\n\n{projects}")

        return "\n\n---\n\n".join(parts) if parts else ""

    # ── 写入（含快照） ───────────────────────────────────

    def update_profile(self, content: str):
        """更新用户画像：旧版存入 history/，然后覆盖写入（原子操作，防写崩）。"""
        self._snapshot(self.profile_path)
        self._atomic_write(self.profile_path, content)

    def update_projects(self, content: str):
        """更新项目状态：旧版存入 history/，然后覆盖写入（原子操作，防写崩）。"""
        self._snapshot(self.projects_path)
        self._atomic_write(self.projects_path, content)

    def list_snapshots(self) -> list[Path]:
        """列出所有历史快照，按时间倒序。"""
        files = sorted(self.history_dir.glob("*.md"), reverse=True)
        return files

    # ── 内部方法 ──────────────────────────────────────────

    def _snapshot(self, filepath: Path):
        """将当前文件内容复制到 history/ 目录，文件名带微秒时间戳。

        只对已有内容的文件做快照（空文件或纯模板文件跳过）。
        如果内容与最近一次快照相同，跳过本次快照。
        """
        if not filepath.exists():
            return

        content = filepath.read_text(encoding="utf-8").strip()
        if not content:
            return

        stem = filepath.stem
        # 检查是否与最新快照内容相同（避免重复快照）
        latest = self._latest_snapshot(stem)
        if latest and latest.read_text(encoding="utf-8").strip() == content:
            return

        # 微秒时间戳，避免同一秒内两次快照文件名碰撞
        timestamp = datetime.now().strftime("%Y-%m-%d-%H%M%S-%f")
        snapshot_path = self.history_dir / f"{stem}-{timestamp}.md"
        snapshot_path.write_text(content, encoding="utf-8")

    def _latest_snapshot(self, stem: str) -> Path | None:
        """找到指定前缀的最新快照文件。"""
        candidates = sorted(
            self.history_dir.glob(f"{stem}-*.md"),
            reverse=True,
        )
        return candidates[0] if candidates else None

    @staticmethod
    def _atomic_write(filepath: Path, content: str):
        """原子写入：先写临时文件，再 rename，防止写崩丢数据。"""
        tmp = filepath.with_suffix(filepath.suffix + ".tmp")
        tmp.write_text(content, encoding="utf-8")
        os.replace(tmp, filepath)  # Windows 上也是原子操作

    def _ensure_file(self, path: Path, default_content: str):
        """如果文件不存在，用默认内容创建。"""
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(default_content, encoding="utf-8")

    @staticmethod
    def _default_profile() -> str:
        return (
            "<!-- 用户画像：告诉 Agent 你是谁、偏好什么、关注什么 -->\n"
            "\n"
            "## 基本信息\n"
            "- 称呼: \n"
            "- 角色: \n"
            "- 时区: \n"
            "\n"
            "## 技能与工具偏好\n"
            "- 编程语言: \n"
            "- 常用工具: \n"
            "- 编辑器/IDE: \n"
            "\n"
            "## 当前关注\n"
            "- \n"
        )

    @staticmethod
    def _default_projects() -> str:
        return (
            "<!-- 项目状态追踪：记录各项目的进度、阻碍、下一步 -->\n"
            "\n"
            "## Personal-Agent\n"
            "- 状态: Phase 4 开发中\n"
            "- 最近进展: Phase 2 核心控制器完成，CLI 对话可用\n"
            "- 下一步: 完成 Memory 系统，接入 Planner\n"
        )
