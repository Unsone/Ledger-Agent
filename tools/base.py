from abc import ABC, abstractmethod


class Tool(ABC):
    """所有工具的统一接口，Executor 依赖这个接口调用工具，不关心具体实现。

    params_schema 声明每个 action 的精确参数：
    Planner 依据它生成正确的参数名，Executor 依据它校验必填参数。
    格式:
        {
            "action名": [
                {"name": "参数名", "required": True/False, "desc": "说明"},
                ...
            ],
        }
    """

    name: str = ""
    description: str = ""
    params_schema: dict = {}

    @abstractmethod
    def execute(self, **kwargs) -> dict:
        """
        统一返回格式:
        {
            "success": bool,
            "result": str | dict,
            "error": str | None
        }
        """
        raise NotImplementedError
