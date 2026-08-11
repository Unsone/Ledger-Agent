"""Memory 测试：快照、原子写入、上下文生成。"""

import pytest
from pathlib import Path
from agent.memory import Memory


class TestMemoryInit:
    """初始化。"""

    def test_creates_default_files(self, temp_memory_dir):
        """初始化时应创建默认 profile 和 projects。"""
        mem = Memory(memory_dir=str(temp_memory_dir))
        assert mem.profile_path.exists()
        assert mem.projects_path.exists()

    def test_history_dir_created(self, temp_memory_dir):
        """history 目录应自动创建。"""
        mem = Memory(memory_dir=str(temp_memory_dir))
        assert mem.history_dir.exists()

    def test_does_not_overwrite_existing(self, temp_memory_dir):
        """不覆盖已有文件。"""
        profile = temp_memory_dir / "profile.md"
        profile.write_text("自定义内容", encoding="utf-8")
        mem = Memory(memory_dir=str(temp_memory_dir))
        assert mem.get_profile() == "自定义内容"


class TestContextGeneration:
    """get_context 上下文生成。"""

    def test_empty_templates_return_empty(self, temp_memory_dir):
        """模板内容不生成上下文。"""
        mem = Memory(memory_dir=str(temp_memory_dir))
        ctx = mem.get_context()
        assert ctx == ""

    def test_filled_profile_generates_context(self, temp_memory_dir):
        """填写后的 profile 出现在上下文中。"""
        mem = Memory(memory_dir=str(temp_memory_dir))
        mem.update_profile("## 基本信息\n- 称呼: 测试用户\n")
        ctx = mem.get_context()
        assert "用户画像" in ctx
        assert "测试用户" in ctx

    def test_both_filled(self, temp_memory_dir):
        """profile 和 projects 都有内容时两者均出现。"""
        mem = Memory(memory_dir=str(temp_memory_dir))
        mem.update_profile("## 基本信息\n- 称呼: 测试\n")
        mem.update_projects("## 项目\n- 状态: 开发中\n")
        ctx = mem.get_context()
        assert "用户画像" in ctx
        assert "项目状态" in ctx


class TestSnapshot:
    """快照机制。"""

    def test_snapshot_created_on_update(self, temp_memory_dir):
        """更新时应创建快照。"""
        mem = Memory(memory_dir=str(temp_memory_dir))
        # 先写点真实内容
        mem.profile_path.write_text("版本1", encoding="utf-8")
        mem.update_profile("版本2")
        snapshots = list(mem.history_dir.glob("profile-*.md"))
        assert len(snapshots) >= 1

    def test_snapshot_contains_old_content(self, temp_memory_dir):
        """快照应包含旧内容。"""
        mem = Memory(memory_dir=str(temp_memory_dir))
        mem.profile_path.write_text("旧版本内容", encoding="utf-8")
        mem.update_profile("新版本内容")
        snapshots = sorted(mem.history_dir.glob("profile-*.md"))
        latest = snapshots[-1]
        assert "旧版本内容" in latest.read_text(encoding="utf-8")

    def test_no_duplicate_snapshot(self, temp_memory_dir):
        """文件内容与最新快照相同时，跳过本次快照。"""
        mem = Memory(memory_dir=str(temp_memory_dir))
        mem.profile_path.write_text("v1", encoding="utf-8")

        # update: snapshots file("v1"), writes "A"
        mem.update_profile("A")
        # 此时 latest snapshot = "v1", file = "A"
        count_after_first = len(list(mem.history_dir.glob("profile-*.md")))

        # update: snapshots file("A"), writes "A" (same content)
        # file("A") != latest("v1") → saves snapshot-A
        mem.update_profile("A")
        # 此时 latest snapshot = "A", file = "A"
        count_after_second = len(list(mem.history_dir.glob("profile-*.md")))
        assert count_after_second == count_after_first + 1

        # update: file("A") == latest("A") → dedup SKIPS!
        mem.update_profile("B")
        count_after_third = len(list(mem.history_dir.glob("profile-*.md")))
        # 第三次应该没有新快照（被去重了）
        assert count_after_third == count_after_second  # dedup worked

    def test_microsecond_timestamps(self, temp_memory_dir):
        """快照文件名包含微秒。"""
        mem = Memory(memory_dir=str(temp_memory_dir))
        mem.profile_path.write_text("v1", encoding="utf-8")
        mem.update_profile("v2")
        snapshots = list(mem.history_dir.glob("profile-*.md"))
        name = snapshots[0].stem  # e.g. "profile-2026-08-12-001200-123456"
        # 文件名应包含微秒部分（6 位数字）
        parts = name.split("-")
        assert len(parts[-1]) == 6  # 微秒


class TestAtomicWrite:
    """原子写入。"""

    def test_atomic_write_does_not_leave_tmp(self, temp_memory_dir):
        """写入后不应留下临时文件。"""
        mem = Memory(memory_dir=str(temp_memory_dir))
        mem.update_profile("测试内容")
        tmp_files = list(temp_memory_dir.glob("*.tmp"))
        assert len(tmp_files) == 0

    def test_file_integrity(self, temp_memory_dir):
        """写入后文件内容应完整。"""
        mem = Memory(memory_dir=str(temp_memory_dir))
        content = "一行\n二行\n三行\n"
        mem.update_profile(content)
        assert mem.get_profile() == content
