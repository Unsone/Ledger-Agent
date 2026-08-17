import os
from openai import OpenAI


class LLM:
    """统一 LLM 接口，Agent 不直接调用具体厂商 SDK。"""

    def __init__(self, config: dict):
        self.provider = config["llm"]["provider"]
        self.model = config["llm"]["model"]
        self.temperature = config["llm"].get("temperature", 0.3)
        self.client = OpenAI(
            api_key=os.getenv("DEEPSEEK_API_KEY"),
            base_url="https://api.deepseek.com" if self.provider == "deepseek" else None,
        )

    def chat(self, messages: list[dict], json_mode: bool = False) -> str:
        """调用 LLM。

        Args:
            messages: OpenAI 格式的消息列表
            json_mode: True 时启用 JSON 输出模式（DeepSeek 的
                response_format，保证返回合法 JSON 对象）

        Returns:
            LLM 回复文本。json_mode 下理论上为纯 JSON。
        """
        kwargs = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
        }
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}

        response = self.client.chat.completions.create(**kwargs)
        content = response.choices[0].message.content
        return content or ""
