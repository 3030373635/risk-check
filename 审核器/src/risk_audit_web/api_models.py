"""REST API 的严格请求模型。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    """禁止未声明字段进入本机系统操作。"""

    model_config = ConfigDict(extra="forbid")


class TaskCreateRequest(StrictModel):
    """描述用户可提交的新任务字段。"""

    display_name: str = Field(min_length=1, max_length=200)
    input_root: str = Field(min_length=1)


class OutputPathPreviewRequest(StrictModel):
    """描述输出路径预览请求。"""

    input_root: str = Field(min_length=1)


class ShutdownRequest(StrictModel):
    """描述退出策略。"""

    mode: Literal["immediate", "cancel_active_tasks"]


class EmptyRequest(StrictModel):
    """描述不允许携带业务字段的修改请求。"""

