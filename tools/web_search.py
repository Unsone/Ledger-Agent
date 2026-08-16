from tools.base import Tool


class WebSearchTool(Tool):
    """网页搜索和内容获取工具。"""

    name = "web_search"
    description = (
        "搜索网页或获取网页内容。支持 search(搜索关键词) 和 fetch(获取URL内容)"
    )

    params_schema = {
        "search": [
            {"name": "query", "required": True, "desc": "搜索关键词"},
            {"name": "max_results", "required": False, "desc": "结果数量，默认 5 最多 10"},
        ],
        "fetch": [
            {"name": "url", "required": True, "desc": "网页 URL"},
        ],
    }

    def execute(self, action: str = "search", query: str = "", url: str = "",
                max_results: int = 5, **kwargs) -> dict:
        """执行网页搜索或内容获取。

        Args:
            action: "search"（搜索）或 "fetch"（获取页面内容）
            query: 搜索关键词（action=search 时必填）
            url: 网页地址（action=fetch 时必填）
            max_results: 搜索结果数量上限（默认 5，最多 10）

        Returns:
            {"success": bool, "result": str, "error": str|None}
        """
        handlers = {
            "search": self._search,
            "fetch": self._fetch,
        }

        handler = handlers.get(action)
        if handler is None:
            return {
                "success": False,
                "result": None,
                "error": f"未知操作: '{action}'。支持: search, fetch",
            }

        try:
            return handler(query=query, url=url, max_results=max_results)
        except Exception as e:
            return {"success": False, "result": None, "error": str(e)}

    @staticmethod
    def _search(query: str, max_results: int = 5, **kwargs) -> dict:
        """DuckDuckGo 文本搜索。"""
        if not query.strip():
            return {"success": False, "result": None, "error": "搜索关键词不能为空"}

        from ddgs import DDGS

        max_results = min(max_results, 10)  # 上限 10 条
        results = []
        with DDGS() as ddgs:
            for r in ddgs.text(query, max_results=max_results):
                results.append(
                    f"标题: {r['title']}\n"
                    f"链接: {r['href']}\n"
                    f"摘要: {r['body'][:200]}"
                )

        if not results:
            return {"success": True, "result": "未找到相关结果。", "error": None}

        output = f"搜索「{query}」共 {len(results)} 条结果：\n\n"
        output += "\n\n---\n\n".join(results)
        return {"success": True, "result": output, "error": None}

    @staticmethod
    def _fetch(url: str, **kwargs) -> dict:
        """获取网页内容（HTML → 纯文本摘要）。"""
        if not url.strip():
            return {"success": False, "result": None, "error": "URL 不能为空"}

        import re
        from urllib.request import urlopen, Request

        # 发送请求
        req = Request(url, headers={"User-Agent": "PersonalAgent/1.0"})
        with urlopen(req, timeout=15) as resp:
            html = resp.read().decode("utf-8", errors="replace")

        # 简单提取文本（去掉标签）
        text = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL | re.I)
        text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL | re.I)
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"\s+", " ", text).strip()

        # 截断，避免上下文溢出
        if len(text) > 3000:
            text = text[:3000] + "\n... [页面内容已截断]"

        return {"success": True, "result": text, "error": None}
