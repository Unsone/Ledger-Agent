from abc import ABC, abstractmethod

class Tool(ABC):
    """所有工具的统一接口，Executor 依赖这个接口调用工具，不关心具体实现。"""

    name: str = ""
    description: str = ""

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
