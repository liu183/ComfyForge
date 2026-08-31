"""API 数据模型（Pydantic）。"""
from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

TaskType = Literal["txt2img", "img2img", "txt2video", "img2video"]
TaskStatus = Literal["pending", "running", "succeeded", "failed"]


class TaskCreate(BaseModel):
    """创建生成任务的请求体。"""

    type: TaskType
    params: dict[str, Any] = Field(default_factory=dict, description="业务参数（提示词、尺寸等）")


class AssetInfo(BaseModel):
    """结果资源。"""

    kind: Literal["image", "video", "gif", "text"] = "image"
    url: str = ""
    filename: str = ""
    note: str = ""


class TaskResult(BaseModel):
    assets: list[AssetInfo] = Field(default_factory=list)
    info: dict[str, Any] = Field(default_factory=dict)


class TaskOut(BaseModel):
    """任务视图。"""

    id: str
    type: str
    params: dict[str, Any]
    backend: str = ""            # 实际执行后端: comfy_local / comfy_api / mock
    status: TaskStatus = "pending"
    progress: Optional[str] = None
    error: Optional[str] = None
    result: Optional[TaskResult] = None
    created_at: float = 0.0
    updated_at: float = 0.0


class CapabilityParam(BaseModel):
    name: str
    label: str
    type: str                       # text / int / float / bool / select / image
    default: Any = None
    required: bool = False
    options: list[Any] = Field(default_factory=list)
    description: str = ""
    min: Optional[float] = None
    max: Optional[float] = None


class CapabilityOut(BaseModel):
    """单个能力视图。"""

    type: str
    label: str
    description: str
    backend: str                    # 当前激活后端
    backend_status: str             # ready / degraded / mock
    backends: list[str]             # 全部可用后端（按优先级）
    reason: str = ""                # 探测说明
    params: list[CapabilityParam] = Field(default_factory=list)
    accepts_image: bool = False


class ServerOut(BaseModel):
    name: str
    url: str
    reachable: bool
    version: str = ""
    error: str = ""


class HealthOut(BaseModel):
    app: str
    version: str
    comfy_servers: list[ServerOut]
    capabilities: list[CapabilityOut]
    force_mock: bool
