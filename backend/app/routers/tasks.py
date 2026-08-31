"""任务接口：创建生成任务、查询任务、结果。"""
from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from starlette.datastructures import UploadFile as StarletteUploadFile

from ..schemas import TaskCreate, TaskOut
from ..services.workflows import probe_capability

router = APIRouter(tags=["tasks"])


def normalize_params(tpl: dict[str, Any], raw: dict[str, Any]) -> dict[str, Any]:
    """按模板参数定义把表单字符串转成正确类型。"""
    specs = {p["name"]: p for p in tpl.get("params", [])}
    out: dict[str, Any] = {}
    for k, v in raw.items():
        spec = specs.get(k)
        if spec is None:
            out[k] = v
            continue
        ptype = spec.get("type")
        try:
            if ptype == "int":
                out[k] = int(v) if v not in ("", None) else spec.get("default")
            elif ptype == "float":
                out[k] = float(v) if v not in ("", None) else spec.get("default")
            elif ptype == "bool":
                out[k] = str(v).lower() in ("1", "true", "yes", "on")
            else:
                out[k] = v
        except (TypeError, ValueError):
            out[k] = v
    return out


@router.post("/tasks", response_model=TaskOut, status_code=201)
async def create_task(
    request: Request,
    body: TaskCreate,
):
    """JSON 方式创建任务（Agent / 系统调用）。"""
    st = request.app.state
    tpl = next((t for t in st.templates if t["type"] == body.type), None)
    if not tpl:
        raise HTTPException(400, f"不支持的能力类型: {body.type}")

    params = normalize_params(tpl, dict(body.params))
    required = [p["name"] for p in tpl.get("params", []) if p.get("required")]
    for r in required:
        if r not in params or params[r] in (None, ""):
            raise HTTPException(422, f"缺少必填参数: {r}")

    backend, _ = probe_capability(
        tpl,
        st.primary_server,
        st.object_info or {},
        st.comfy_servers,
        st.object_infos or {},
    )
    task = await st.task_manager.create(body.type, params, backend)
    return task


@router.post("/tasks/upload", response_model=TaskOut, status_code=201)
async def create_task_with_image(request: Request):
    """multipart 方式创建任务：图片文件 + 动态文本参数（Web 前端用）。"""
    st = request.app.state
    form = await request.form()

    task_type = str(form.get("type") or "")
    tpl = next((t for t in st.templates if t["type"] == task_type), None)
    if not tpl:
        raise HTTPException(400, f"不支持的能力类型: {task_type}")

    image_files = [
        v for v in form.values() if isinstance(v, StarletteUploadFile)
    ]
    if not image_files:
        raise HTTPException(422, "缺少图片文件（字段名 image）")
    image = image_files[0]
    content = await image.read()
    if len(content) > 15 * 1024 * 1024:
        raise HTTPException(413, "图片过大（上限 15MB）")

    raw = {
        k: v for k, v in form.items()
        if not isinstance(v, StarletteUploadFile) and k != "type"
    }
    params = normalize_params(tpl, raw)

    backend, _ = probe_capability(
        tpl,
        st.primary_server,
        st.object_info or {},
        st.comfy_servers,
        st.object_infos or {},
    )
    task = await st.task_manager.create(
        task_type, params, backend, image_bytes=content
    )
    return task


@router.get("/tasks", response_model=list[TaskOut])
async def list_tasks(
    request: Request, status: Optional[str] = None, limit: int = 100
):
    st = request.app.state
    return st.task_manager.list(status=status, limit=limit)


@router.get("/tasks/{task_id}", response_model=TaskOut)
async def get_task(request: Request, task_id: str):
    st = request.app.state
    task = st.task_manager.get(task_id)
    if not task:
        raise HTTPException(404, "任务不存在")
    return task
