"""RAG 测试：切块、降级嵌入器、增量索引、语义检索（离线，无模型下载）。"""

import pytest
from pathlib import Path
from agent.rag import (
    chunk_markdown, KeywordEmbedder, RAGStore, VECTOR_DIM,
)
from tools.memory_search import MemorySearchTool


@pytest.fixture
def sources(tmp_path):
    """创建源目录：两个项目笔记 + 一个日记。"""
    src = tmp_path / "sources"
    (src / "Projects").mkdir(parents=True)
    (src / "Daily").mkdir()

    (src / "Projects" / "alpha.md").write_text(
        "# Alpha 项目\n\n## 状态\n\n开发中，Phase 2 完成。\n\n"
        "## 下一步\n\n接入登录系统。\n",
        encoding="utf-8",
    )
    (src / "Projects" / "beta.md").write_text(
        "# Beta 项目\n\n## 状态\n\n已上线，运行稳定。\n",
        encoding="utf-8",
    )
    (src / "Daily" / "2026-08-17.md").write_text(
        "# 日记\n\n## 任务执行记录\n\n目标: 修复登录 bug\n",
        encoding="utf-8",
    )
    return src


@pytest.fixture
def rag(sources, tmp_path):
    """用关键词嵌入器（离线确定性）构建 RAGStore。"""
    return RAGStore(
        source_dirs=[str(sources)],
        persist_dir=str(tmp_path / "vector_store"),
        embedder=KeywordEmbedder(),
    )


class TestChunking:
    """markdown 切块。"""

    def test_split_by_heading(self):
        chunks = list(chunk_markdown(
            "# 标题一\n\n内容A\n\n## 标题二\n\n内容B\n", "test.md"
        ))
        assert len(chunks) >= 2
        headings = {c["heading"] for c in chunks}
        assert "标题一" in headings or any("标题" in h for h in headings)

    def test_chunk_size_capped(self):
        """超长文本应被切成多块。"""
        long_text = "# 长文\n\n" + ("很长的内容。" * 200)
        chunks = list(chunk_markdown(long_text, "test.md"))
        assert len(chunks) > 1
        for c in chunks:
            assert len(c["text"]) <= 500  # CHUNK_SIZE=400 + 容差

    def test_empty_text(self):
        chunks = list(chunk_markdown("", "test.md"))
        assert chunks == []

    def test_metadata_fields(self):
        chunks = list(chunk_markdown("# T\n\ntext\n", "test.md"))
        for c in chunks:
            assert "text" in c and "heading" in c and "source" in c
            assert c["source"] == "test.md"


class TestKeywordEmbedder:
    """降级嵌入器。"""

    def test_fixed_dimension(self):
        e = KeywordEmbedder()
        vecs = e.embed(["hello", "world"])
        assert len(vecs) == 2
        assert len(vecs[0]) == VECTOR_DIM

    def test_deterministic(self):
        e = KeywordEmbedder()
        v1 = e.embed(["同一段文本"])[0]
        v2 = e.embed(["同一段文本"])[0]
        assert v1 == v2

    def test_similar_texts_closer(self):
        """相似文本的余弦距离应小于无关文本。"""
        e = KeywordEmbedder()

        def cos(a, b):
            return sum(x * y for x, y in zip(a, b))

        q = e.embed(["登录系统进展"])[0]
        similar = e.embed(["接入登录系统"])[0]
        unrelated = e.embed(["天气不错去散步"])[0]
        assert cos(q, similar) > cos(q, unrelated)


class TestRAGStore:
    """存储与检索。"""

    def test_index_and_search(self, rag):
        stats = rag.index()
        assert stats["indexed"] == 3  # alpha, beta, daily
        assert stats["total_chunks"] > 0

    def test_search_relevant(self, rag):
        rag.index()
        hits = rag.search("Alpha 项目做到哪一步了？", top_k=3)
        assert len(hits) > 0
        # 最相关的应来自 alpha.md
        assert any("alpha.md" in h["source"] for h in hits)

    def test_search_returns_metadata(self, rag):
        rag.index()
        hits = rag.search("登录系统", top_k=1)
        assert hits[0]["text"]
        assert hits[0]["source"]
        assert "heading" in hits[0]

    def test_incremental_index(self, rag, sources):
        """未变更文件不重建索引，变更文件重建。"""
        rag.index()
        stats = rag.index()
        assert stats["indexed"] == 0  # 全部未变化

        # 修改一个文件
        (sources / "Projects" / "alpha.md").write_text(
            "# Alpha 项目\n\n## 状态\n\n已完成！\n", encoding="utf-8"
        )
        stats = rag.index()
        assert stats["indexed"] == 1  # 只重建变更的

    def test_removed_file_cleanup(self, rag, sources):
        """删除的文件应从索引中移除。"""
        rag.index()
        before = rag.collection.count()

        (sources / "Projects" / "beta.md").unlink()
        stats = rag.index()
        assert stats["removed"] == 1
        assert rag.collection.count() < before

    def test_search_triggers_index(self, rag):
        """未索引时 search 自动触发索引。"""
        assert rag.collection.count() == 0
        hits = rag.search("Alpha")
        assert len(hits) > 0

    def test_blank_query_returns_no_hits(self, rag):
        rag.index()
        assert rag.search("   ") == []

    def test_answer_with_mock_llm(self, rag):
        """RAG 问答：检索 + LLM 生成。"""
        rag.index()

        class MockLLM:
            def __init__(self):
                self.last_messages = None

            def chat(self, messages, json_mode=False):
                self.last_messages = messages
                user_content = messages[1]["content"]
                assert "Alpha 项目" in user_content  # 检索片段已注入 prompt
                return "Alpha 项目开发中，Phase 2 已完成。"

        llm = MockLLM()
        answer = rag.answer("Alpha 项目做到哪一步了？", llm)
        assert "Phase 2" in answer

    def test_index_skips_inbox(self, sources, tmp_path):
        """Inbox.md 不应被索引（是待办不是知识）。"""
        (sources / "Inbox.md").write_text("临时任务", encoding="utf-8")
        r = RAGStore(
            source_dirs=[str(sources)],
            persist_dir=str(tmp_path / "vs2"),
            embedder=KeywordEmbedder(),
        )
        r.index()
        hits = r.search("临时任务", top_k=3)
        # 不应有 Inbox 的内容
        assert not any("Inbox.md" in h["source"] for h in hits)


class TestMemorySearchTool:
    def test_returns_search_results(self, rag):
        result = MemorySearchTool(rag).execute(query="Alpha 项目")
        assert result["success"] is True
        assert "alpha.md" in result["result"]

    def test_rejects_blank_query(self, rag):
        result = MemorySearchTool(rag).execute(query="")
        assert result["success"] is False
        assert result["error"] == "query 不能为空"

    def test_rejects_invalid_top_k(self, rag):
        result = MemorySearchTool(rag).execute(query="Alpha", top_k=0)
        assert result["success"] is False
        assert result["error"] == "top_k 必须是正整数"
