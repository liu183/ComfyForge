"""资源接口：ComfyUI 产物代理 + mock 本地产物 + 图片上传。"""
from __future__ import annotations

import mimetypes
import os

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, Response

router = APIRouter(prefix="/assets", tags=["assets"])


@router.get("/mock/{filename}")
async def mock_asset(request: Request, filename: str):
    """返回本地 mock 生成的产物。"""
    st = request.app.state
    safe = os.path.basename(filename)  # 防目录穿越
    path = os.path.join(st.assets_dir, safe)
    if not os.path.isfile(path):
        raise HTTPException(404, "资源不存在")
    mime, _ = mimetypes.guess_type(path)
    return FileResponse(path, media_type=mime or "application/octet-stream")


@router.get("/proxy")
async def proxy_asset(
    request: Request,
    filename: str,
    subfolder: str = "",
    type: str = "output",
    node: str = "",
):
    """从 ComfyUI /view 代理拉取产物，避免前端直连节点。

    node 指定节点名（如 comfy_local / comfy_h3），缺省用主节点。
    """
    st = request.app.state
    servers = getattr(st, "comfy_servers", {})
    server = servers.get(node) if node else None
    if server is None:
        server = st.primary_server
    if not server or not server.reachable:
        raise HTTPException(503, "ComfyUI 节点不可达")
    try:
        data = await server.view_image(
            filename, subfolder=subfolder, img_type=type
        )
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"拉取资源失败: {e}") from e
    mime, _ = mimetypes.guess_type(filename)
    return Response(
        content=data,
        media_type=mime or "application/octet-stream",
    )


@router.post("/upload")
async def upload_image(request: Request, image: UploadFile = File(...)):
    """上传图片，返回可引用地址（后续任务可通过引用使用）。"""
    st = request.app.state
    content = await image.read()
    if len(content) > 15 * 1024 * 1024:
        raise HTTPException(413, "图片过大（上限 15MB）")
    import uuid

    ext = os.path.splitext(image.filename or "")[1] or ".png"
    name = f"upload_{uuid.uuid4().hex[:10]}{ext}"
    with open(os.path.join(st.assets_dir, name), "wb") as f:
        f.write(content)
    return {"url": f"/api/v1/assets/mock/{name}", "filename": name}
