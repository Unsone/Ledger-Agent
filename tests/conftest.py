"""共享 fixtures 和测试工具。"""

import sys
import os
from pathlib import Path
import pytest

# 确保项目根在 sys.path 中
sys.path.insert(0, str(Path(__file__).parent.parent))

# Windows 下强制 UTF-8
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


@pytest.fixture
def temp_vault(tmp_path):
    """创建临时 Obsidian vault 目录结构。"""
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "Daily").mkdir()
    (vault / "Projects").mkdir()
    (vault / "Knowledge").mkdir()
    return vault


@pytest.fixture
def temp_memory_dir(tmp_path):
    """创建临时 memory 目录。"""
    mem = tmp_path / "memory"
    mem.mkdir()
    (mem / "history").mkdir()
    return mem


@pytest.fixture
def sample_safety_config(tmp_path):
    """创建临时 safety.yaml。"""
    import yaml
    config = {
        "blocked_patterns": ["rm -rf", "shutdown", "format "],
        "confirm_patterns": ["git push", "git reset --hard", "pip install"],
    }
    path = tmp_path / "safety.yaml"
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(config, f)
    return str(path)


@pytest.fixture
def mock_llm():
    """创建一个假的 LLM 实例，chat() 返回预设值。"""

    class MockLLM:
        def __init__(self):
            self.call_count = 0
            self.messages_history = []
            self.next_response = "{}"

        def chat(self, messages):
            self.call_count += 1
            self.messages_history.append(messages)
            return self.next_response

    return MockLLM()
