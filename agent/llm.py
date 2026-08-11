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

    def chat(self, messages: list[dict]) -> str:
        response = self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            temperature=self.temperature,
        )
        return response.choices[0].message.content
