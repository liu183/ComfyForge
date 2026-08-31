"""ComfyUI HTTP 客户端：封装 /prompt、/history、/view、/upload/image、/object_info。"""
from __future__ import annotations

import asyncio
import uuid
from typing import Any, Optional

import httpx

from ..config import settings


class ComfyServer:
    def __init__(self, name: str, base_url: str):
        self.name = name
        self.base_url = base_url
        self.version = ""
        self.reachable = False
        self.error = ""

    async def check(self) -> bool:
        """探测节点是否可达，并缓存版本信息。"""
        try:
            async with httpx.AsyncClient(timeout=settings.comfy_timeout) as client:
                r = await client.get(f"{self.base_url}/system_stats")
                if r.status_code == 200:
                    data = r.json()
                    self.version = (
                        data.get("system", {}).get("comfyui_version", "") or ""
                    )
                    self.reachable = True
                    self.error = ""
                    return True
                self.error = f"HTTP {r.status_code}"
        except Exception as e:  # noqa: BLE001
            self.error = str(e)[:200]
        self.reachable = False
        return False

    async def object_info(self) -> dict[str, Any]:
        """获取节点定义（含各 loader 的模型选项）。"""
        async with httpx.AsyncClient(timeout=settings.comfy_timeout) as client:
            r = await client.get(f"{self.base_url}/object_info")
            r.raise_for_status()
            return r.json()

    async def submit(self, workflow: dict[str, Any]) -> str:
        """提交 workflow，返回 prompt_id。"""
        payload = {"prompt": workflow, "client_id": str(uuid.uuid4())}
        async with httpx.AsyncClient(timeout=settings.comfy_timeout) as client:
            r = await client.post(
                f"{self.base_url}/prompt", json=payload
            )
            if r.status_code != 200:
                detail = r.text[:800]
                try:
                    err = r.json().get("error", {})
                    node_errs = r.json().get("node_errors", {})
                    if node_errs:
                        msgs = []
                        for nid, info in node_errs.items():
                            for e in info.get("errors", []):
                                msgs.append(f"节点{nid} {e.get('message','')}: {e.get('details','')}")
                        detail = "; ".join(msgs[:5]) or detail
                    elif err:
                        detail = str(err.get("message", detail))
                except Exception:  # noqa: BLE001
                    pass
                raise RuntimeError(
                    f"ComfyUI 提交失败（HTTP {r.status_code}）: {detail[:600]}"
                )
            data = r.json()
            if "error" in data:
                raise RuntimeError(f"ComfyUI 提交失败: {data['error']}")
            if "node_errors" in data and data.get("node_errors"):
                raise RuntimeError(
                    f"ComfyUI 节点错误: {json_dumps(data['node_errors'])[:600]}"
                )
            return data["prompt_id"]

    async def history(self, prompt_id: str) -> dict[str, Any]:
        """查询单次执行历史。"""
        async with httpx.AsyncClient(timeout=settings.comfy_timeout) as client:
            r = await client.get(f"{self.base_url}/history/{prompt_id}")
            r.raise_for_status()
            return r.json()

    async def view_image(
        self, filename: str, subfolder: str = "", img_type: str = "output"
    ) -> bytes:
        """获取生成资源（图片/视频）。"""
        params = {"filename": filename, "type": img_type}
        if subfolder:
            params["subfolder"] = subfolder
        async with httpx.AsyncClient(timeout=settings.comfy_timeout) as client:
            r = await client.get(f"{self.base_url}/view", params=params)
            r.raise_for_status()
            return r.content

    async def upload_image(
        self, filename: str, content: bytes, subfolder: str = ""
    ) -> dict[str, Any]:
        """上传图片到 ComfyUI input 目录（供 LoadImage 使用）。"""
        files = {"image": (filename, content, "image/png")}
        data = {"overwrite": "true", "type": "input"}
        if subfolder:
            data["subfolder"] = subfolder
        async with httpx.AsyncClient(timeout=settings.comfy_timeout) as client:
            r = await client.post(
                f"{self.base_url}/upload/image", files=files, data=data
            )
            r.raise_for_status()
            return r.json()

    def __repr__(self) -> str:  # pragma: no cover
        return f"<ComfyServer {self.name}={self.base_url} reachable={self.reachable}>"


def json_dumps(obj: Any) -> str:
    import json

    return json.dumps(obj, ensure_ascii=False)
