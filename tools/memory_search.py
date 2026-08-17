from tools.base import Tool


class MemorySearchTool(Tool):
    """RAG 记忆检索工具：语义搜索 memory 与 Obsidian 笔记。"""

    name = "memory_search"
    description = (
        "语义检索长期记忆库（memory 与 Obsidian 笔记），"
        "回答\"某项目做到哪一步了\"之类的跨会话历史问题"
    )

    params_schema = {
        "search": [
            {"name": "query", "required": True, "desc": "检索问题或关键词"},
            {"name": "top_k", "required": False, "desc": "返回片段数，默认 5"},
        ],
    }

    def __init__(self, rag_store=None):
        self.rag_store = rag_store  # 由 agent 注入 RAGStore 实例

    def execute(self, action: str = "search", query: str = "",
                top_k: int = 5, **kwargs) -> dict:
        if action != "search":
            return {"success": False, "result": None, "error": f"未知操作: '{action}'。只支持 search"}
        if not self.rag_store:
            return {"success": False, "result": None, "error": "RAGStore 未注入"}
        if not query.strip():
            return {"success": False, "result": None, "error": "query 不能为空"}
        if not isinstance(top_k, int) or top_k < 1:
            return {"success": False, "result": None, "error": "top_k 必须是正整数"}

        try:
            hits = self.rag_store.search(query, top_k=top_k)
            if not hits:
                return {"success": True, "result": "记忆库中没有找到相关信息。", "error": None}

            parts = []
            for i, h in enumerate(hits, 1):
                from pathlib import Path
                fname = Path(h["source"]).name
                parts.append(f"[{i}] {fname}（{h['heading']}）:\n{h['text']}")
            return {"success": True, "result": "\n\n".join(parts), "error": None}
        except Exception as e:
            return {"success": False, "result": None, "error": str(e)}
