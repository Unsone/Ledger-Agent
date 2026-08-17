"""Planner 输出的 Pydantic 结构化模型。

替代手工 _validate：类型安全、错误信息清晰、
运行时约束（max_steps / 合法工具列表）通过 validation context 注入。
"""

from typing import Literal, Any
from pydantic import BaseModel, Field, ValidationInfo, model_validator


RiskLevel = Literal["low", "medium", "high"]


class Step(BaseModel):
    """单个执行步骤。"""

    id: int = Field(description="步骤编号，从 1 开始")
    action: str = Field(min_length=1, description="该步骤要做什么")
    tool: str = Field(min_length=1, description="工具名称")
    params: dict[str, Any] = Field(
        default_factory=dict, description="传给工具的参数"
    )
    risk: RiskLevel = Field(default="low", description="风险等级")

    @model_validator(mode="after")
    def check_tool_allowed(self, info: ValidationInfo):
        """校验 tool 必须在可用工具列表中（或 none）。"""
        valid_tools = (info.context or {}).get("valid_tools")
        if valid_tools is not None and self.tool not in valid_tools:
            raise ValueError(
                f"tool='{self.tool}' 不在可用工具列表中"
                f" ({', '.join(sorted(valid_tools))})"
            )
        return self


class Plan(BaseModel):
    """完整计划。"""

    goal: str = Field(min_length=1, description="任务目标概括")
    steps: list[Step] = Field(min_length=1, description="步骤列表")

    @model_validator(mode="after")
    def check_max_steps(self, info: ValidationInfo):
        """强制步骤数上限（config.yaml 的 agent.max_steps）。"""
        max_steps = (info.context or {}).get("max_steps")
        if max_steps is not None and len(self.steps) > max_steps:
            raise ValueError(
                f"步骤数 {len(self.steps)} 超过最大限制 {max_steps}，请合并或精简"
            )
        return self
