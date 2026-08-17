"""RAG 长期记忆：将 memory 与 Obsidian 笔记向量化，支持跨会话语义问答。

设计：
- 存储：ChromaDB 本地持久化（memory/vector_store/），增量索引（按 mtime）
- 嵌入：fastembed + bge-small-zh（中文语义，ONNX 无 PyTorch 依赖）
  模型不可用时自动降级为关键词嵌入（确定性哈希，离线可用）
- 切块：按 markdown 标题切分，段落聚合，~400 字符一块
"""

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Iterator


# ── 切块 ────────────────────────────────────────────────

CHUNK_SIZE = 400  # 每块最大字符数


def chunk_markdown(text: str, source: str) -> Iterator[dict]:
    """将 markdown 文本切为块。

    按标题（#、##）分段，段内按段落聚合到 CHUNK_SIZE 上限。
    产出: {"text": 块内容, "heading": 所属标题, "source": 来源文件}
    """
    lines = text.split("\n")
    current_heading = "(开头)"
    buffer: list[str] = []
    buffer_len = 0

    def flush() -> dict | None:
        nonlocal buffer, buffer_len
        if not buffer:
            return None
        chunk_text = "\n".join(buffer).strip()
        buffer, buffer_len = [], 0
        if not chunk_text:
            return None
        return {"text": chunk_text, "heading": current_heading, "source": source}

    for line in lines:
        stripped = line.strip()

        # 标题行 → 开启新段
        if stripped.startswith("#"):
            chunk = flush()
            if chunk:
                yield chunk
            current_heading = stripped.lstrip("#").strip()
            buffer.append(line)
            buffer_len += len(line)
            continue

        # 空行 → 段落边界
        if not stripped:
            if buffer and buffer_len >= CHUNK_SIZE * 0.7:
                chunk = flush()
                if chunk:
                    yield chunk
            elif buffer:
                buffer.append("")  # 保留段内空行
            continue

        # 累积超限 → 输出当前块
        if buffer_len + len(line) > CHUNK_SIZE and buffer:
            chunk = flush()
            if chunk:
                yield chunk

        # 单行超限（如无换行的长文本）→ 硬切分
        while len(line) > CHUNK_SIZE:
            buffer.append(line[:CHUNK_SIZE])
            buffer_len += CHUNK_SIZE
            chunk = flush()
            if chunk:
                yield chunk
            line = line[CHUNK_SIZE:]

        buffer.append(line)
        buffer_len += len(line) + 1

    chunk = flush()
    if chunk:
        yield chunk


# ── 嵌入器 ──────────────────────────────────────────────

VECTOR_DIM = 256  # 关键词降级嵌入器的维度


class KeywordEmbedder:
    """降级嵌入器：字符 bigram 哈希到固定维向量。

    无模型、无网络、确定性——用于测试和模型不可用时的兜底。
    中文按单字+双字组合，英文按词，保证一定语义重叠。
    """

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._embed_one(t) for t in texts]

    @staticmethod
    def _embed_one(text: str) -> list[float]:
        vec = [0.0] * VECTOR_DIM
        text = text.lower()
        # 字符 bigram（中文效果：相邻双字共享语义片段）
        grams = [text[i:i + 2] for i in range(max(len(text) - 1, 0))]
        grams += [c for c in text if c.strip()]  # 单字也计入
        for g in grams:
            h = int(hashlib.md5(g.encode("utf-8")).hexdigest(), 16)
            idx = h % VECTOR_DIM
            vec[idx] += 1.0
        # L2 归一化
        norm = sum(v * v for v in vec) ** 0.5
        if norm > 0:
            vec = [v / norm for v in vec]
        return vec


class FastEmbedEmbedder:
    """fastembed + bge-small-zh：中文语义嵌入（首次使用自动下载模型）。"""

    MODEL = "BAAI/bge-small-zh-v1.5"

    def __init__(self):
        from fastembed import TextEmbedding
        self.model = TextEmbedding(model_name=self.MODEL)

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [list(v) for v in self.model.embed(texts)]


def get_embedder():
    """按优先级获取嵌入器：fastembed → 关键词降级。"""
    try:
        return FastEmbedEmbedder()
    except Exception:
        return KeywordEmbedder()


# ── RAG 存储 ────────────────────────────────────────────

class RAGStore:
    """ChromaDB 向量存储：增量索引 + 语义检索。"""

    COLLECTION = "long_term_memory"

    def __init__(self, source_dirs: list[str], persist_dir: str = None,
                 embedder=None):
        if persist_dir is None:
            persist_dir = Path(__file__).parent.parent / "memory" / "vector_store"
        self.persist_dir = Path(persist_dir)
        self.persist_dir.mkdir(parents=True, exist_ok=True)
        self.source_dirs = [Path(d) for d in source_dirs]

        import chromadb
        self.client = chromadb.PersistentClient(path=str(self.persist_dir))
        self.collection = self.client.get_or_create_collection(
            self.COLLECTION,
            metadata={"hnsw:space": "cosine"},
        )
        self.embedder = embedder or get_embedder()

        # 索引状态：source -> mtime（用于增量更新）
        self.state_path = self.persist_dir / "index_state.json"
        self._state: dict = {}
        if self.state_path.exists():
            self._state = json.loads(self.state_path.read_text(encoding="utf-8"))

    # ── 索引 ─────────────────────────────────────────────

    def index(self, force: bool = False) -> dict:
        """扫描源目录，增量索引 markdown 文件。

        Returns: {"indexed": 新增/更新文件数, "removed": 删除文件数, "total_chunks": 块总数}
        """
        current_files: dict[str, float] = {}

        # 扫描所有源文件
        for d in self.source_dirs:
            if not d.exists():
                continue
            for f in d.rglob("*.md"):
                if f.name == "Inbox.md":
                    continue  # 收件箱是待办不是知识
                rel = str(f.resolve())
                current_files[rel] = f.stat().st_mtime

        indexed = 0
        removed = 0

        # 1. 删除已不存在的文件
        stale = set(self._state.keys()) - set(current_files.keys())
        for rel in stale:
            self._remove_source(rel)
            del self._state[rel]
            removed += 1

        # 2. 索引新增或变更的文件
        for rel, mtime in current_files.items():
            if not force and self._state.get(rel) == mtime:
                continue  # 未变化，跳过
            if self._index_file(Path(rel), mtime):
                self._state[rel] = mtime
                indexed += 1

        # 3. 保存索引状态
        self.state_path.write_text(
            json.dumps(self._state, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        return {
            "indexed": indexed,
            "removed": removed,
            "total_chunks": self.collection.count(),
        }

    def _index_file(self, path: Path, mtime: float) -> bool:
        """索引单个文件：删旧块 → 切块 → 嵌入 → 写入。

        Returns:
            True 表示文件已成功处理；读取失败时保留旧索引状态，便于下次重试。
        """
        self._remove_source(str(path.resolve()))
        try:
            text = path.read_text(encoding="utf-8")
        except Exception:
            return False

        chunks = list(chunk_markdown(text, str(path.resolve())))
        if not chunks:
            return True

        ids, docs, metas, embeddings = [], [], [], []
        for i, c in enumerate(chunks):
            cid = f"{hashlib.md5(f'{path}:{mtime}:{i}'.encode()).hexdigest()}"
            ids.append(cid)
            docs.append(c["text"])
            metas.append({
                "source": c["source"],
                "heading": c["heading"],
                "mtime": mtime,
            })
            embeddings.append(self.embedder.embed([c["text"]])[0])

        self.collection.upsert(
            ids=ids, documents=docs, metadatas=metas, embeddings=embeddings
        )
        return True

    def _remove_source(self, source: str):
        """删除指定来源文件的所有块。"""
        existing = self.collection.get(where={"source": source})
        if existing and existing["ids"]:
            self.collection.delete(ids=existing["ids"])

    # ── 检索 ─────────────────────────────────────────────

    def search(self, query: str, top_k: int = 5) -> list[dict]:
        """语义检索最相关的片段。

        Returns: [{"text": ..., "source": ..., "heading": ...}, ...]
        """
        if not query.strip() or top_k < 1:
            return []

        if self.collection.count() == 0:
            self.index()

        if self.collection.count() == 0:
            return []

        qv = self.embedder.embed([query])[0]
        results = self.collection.query(
            query_embeddings=[qv], n_results=min(top_k, 20)
        )

        out = []
        if results and results["documents"]:
            docs = results["documents"][0]
            metas = results["metadatas"][0]
            for doc, meta in zip(docs, metas):
                out.append({
                    "text": doc,
                    "source": meta.get("source", "?"),
                    "heading": meta.get("heading", ""),
                })
        return out

    # ── RAG 问答 ─────────────────────────────────────────

    def answer(self, question: str, llm, top_k: int = 5) -> str:
        """检索 + LLM 生成：完整的 RAG 问答。"""
        hits = self.search(question, top_k=top_k)
        if not hits:
            return "记忆库中没有找到相关信息。"

        context_parts = []
        for i, h in enumerate(hits, 1):
            context_parts.append(
                f"[片段 {i}] 来源: {Path(h['source']).name}"
                f"（{h['heading']}）\n{h['text']}"
            )
        context = "\n\n---\n\n".join(context_parts)

        prompt = (
            f"根据以下记忆库片段回答问题。\n\n"
            f"记忆库片段：\n{context}\n\n"
            f"问题：{question}\n\n"
            f"要求：只依据片段内容回答；片段中没有的信息明确说不知道；"
            f"回答中注明信息来源文件名。"
        )
        return llm.chat([
            {"role": "system", "content": "你是记忆检索助手，只依据提供的片段回答问题。"},
            {"role": "user", "content": prompt},
        ])
