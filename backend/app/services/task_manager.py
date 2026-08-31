"""任务注册表 + 后台调度（内存实现，MVP 够用）。"""
from __future__ import annotations

import asyncio
import time
import uuid
from typing import Any, Optional

from ..config import settings
from ..schemas import TaskOut, TaskResult
from .executor import ComfyExecutor, MockGenerator

# 能力类型 -> 预期输出类型（mock 用）
_TYPE_KIND = {
    "txt2img": "image",
    "img2img": "image",
    "txt2video": "video",
    "img2video": "video",
}


class TaskManager:
    def __init__(self, assets_dir, templates: dict[str, dict[str, Any]]):
        self._tasks: dict[str, dict[str, Any]] = {}
        self._templates = templates
        self.mock_gen = MockGenerator(assets_dir)
        self.comfy_executor: Optional[ComfyExecutor] = None
        self.assets_dir = assets_dir

    # ---- 查询 ----
    def get(self, task_id: str) -> Optional[TaskOut]:
        t = self._tasks.get(task_id)
        return self._to_out(t) if t else None

    def list(self, status: Optional[str] = None, limit: int = 100) -> list[TaskOut]:
        items = list(self._tasks.values())
        items.sort(key=lambda x: x.get("created_at", 0), reverse=True)
        if status:
            items = [t for t in items if t.get("status") == status]
        return [self._to_out(t) for t in items[:limit]]

    # ---- 创建 ----
    async def create(
        self,
        task_type: str,
        params: dict[str, Any],
        backend: str,
        image_bytes: Optional[bytes] = None,
    ) -> TaskOut:
        now = time.time()
        t = {
            "id": uuid.uuid4().hex[:12],
            "type": task_type,
            "params": params,
            "backend": backend,
            "status": "pending",
            "progress": None,
            "error": None,
            "result": None,
            "created_at": now,
            "updated_at": now,
            "_image_bytes": image_bytes,
        }
        self._tasks[t["id"]] = t
        asyncio.create_task(self._run(t))
        return self._to_out(t)

    # ---- 后台执行 ----
    async def _run(self, t: dict[str, Any]) -> None:
        t["status"] = "running"
        t["updated_at"] = time.time()
        try:
            template = self._templates.get(t["type"])
            if not template:
                raise RuntimeError(f"未知能力类型: {t['type']}")

            if t["backend"] == "mock":
                result = await self.mock_gen.generate(
                    _TYPE_KIND.get(t["type"], "image"), t["params"]
                )
            else:
                if not self.comfy_executor:
                    raise RuntimeError("ComfyUI 执行器未初始化")
                result = await self.comfy_executor.run(
                    template,
                    t["backend"],
                    t["params"],
                    t.get("_image_bytes"),
                )
            t["result"] = result.model_dump()
            t["status"] = "succeeded"
        except Exception as e:  # noqa: BLE001
            t["status"] = "failed"
            t["error"] = str(e)[:1000]
        finally:
            t["_image_bytes"] = None
            t["updated_at"] = time.time()

    # ---- 清理 ----
    def cleanup_expired(self) -> int:
        cutoff = time.time() - settings.task_ttl_seconds
        ids = [
            k for k, v in self._tasks.items()
            if v.get("created_at", 0) < cutoff
            and v.get("status") in ("succeeded", "failed")
        ]
        for k in ids:
            self._tasks.pop(k, None)
        return len(ids)

    @staticmethod
    def _to_out(t: dict[str, Any]) -> TaskOut:
        return TaskOut(
            id=t["id"],
            type=t["type"],
            params=t.get("params", {}),
            backend=t.get("backend", ""),
            status=t.get("status", "pending"),
            progress=t.get("progress"),
            error=t.get("error"),
            result=TaskResult(**t["result"]) if t.get("result") else None,
            created_at=t.get("created_at", 0.0),
            updated_at=t.get("updated_at", 0.0),
        )
