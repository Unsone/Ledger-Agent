"""PersonalAgent 普通对话历史管理测试（无需加载配置、RAG 或 API）。"""

import pytest

from agent.agent import PersonalAgent


class StubLLM:
    """记录每次请求的快照，并返回可区分的固定回复。"""

    def __init__(self, fail=False):
        self.requests = []
        self.fail = fail

    def chat(self, messages, json_mode=False):
        self.requests.append([message.copy() for message in messages])
        if self.fail:
            raise RuntimeError("模拟 LLM 失败")
        return f"reply-{len(self.requests)}"


def make_agent(max_history_turns=2, llm=None):
    """构造仅含对话依赖的 Agent，隔离初始化时的文件与网络依赖。"""
    agent = PersonalAgent.__new__(PersonalAgent)
    agent.max_history_turns = max_history_turns
    agent.llm = llm or StubLLM()
    agent.messages = [{"role": "system", "content": "system context"}]
    return agent


def test_history_keeps_system_and_recent_complete_turns():
    agent = make_agent(max_history_turns=2)

    for message in ["u1", "u2", "u3"]:
        agent.chat(message)

    assert agent.messages == [
        {"role": "system", "content": "system context"},
        {"role": "user", "content": "u2"},
        {"role": "assistant", "content": "reply-2"},
        {"role": "user", "content": "u3"},
        {"role": "assistant", "content": "reply-3"},
    ]


def test_request_is_trimmed_before_llm_call():
    llm = StubLLM()
    agent = make_agent(max_history_turns=2, llm=llm)

    for message in ["u1", "u2", "u3"]:
        agent.chat(message)

    assert [item["content"] for item in llm.requests[-1]] == [
        "system context", "u2", "reply-2", "u3"
    ]


def test_failed_request_restores_existing_history():
    llm = StubLLM(fail=True)
    agent = make_agent(llm=llm)
    original = [
        {"role": "system", "content": "system context"},
        {"role": "user", "content": "previous question"},
        {"role": "assistant", "content": "previous answer"},
    ]
    agent.messages = [message.copy() for message in original]

    with pytest.raises(RuntimeError, match="模拟 LLM 失败"):
        agent.chat("new question")

    assert agent.messages == original


@pytest.mark.parametrize("value", [0, -1, "2"])
def test_invalid_history_limit_is_rejected(value):
    agent = make_agent(max_history_turns=value)
    with pytest.raises(ValueError):
        agent._trim_history()
