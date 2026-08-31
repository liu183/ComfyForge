"""主流兼容 API 层。

让消费侧可以直接适配：
- OpenAI Images 风格：POST /v1/images/generations（同步）、POST /v1/images/edits（multipart）
- 主流视频生成风格（MiniMax H3 / 火山 Seedance / Runway 参数语义）：
  POST /v1/videos/generations（异步创建，content[] 多模态数组）、GET /v1/videos/generations/{id}（轮询）

内部统一翻译为 ComfyForge 的任务模型（txt2img / img2img / txt2video / img2video）。
"""
from __future__ import annotations

import asyncio
import base64
import re
import time
from typing import Any, Optional

import httpx
from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel, Field

from ..schemas import TaskOut
from ..services.workflows import probe_capability

router = APIRouter(tags=["compat"])

DEFAULT_IMAGE_MODEL = "AWPainting 1.4\\AWPainting_v1.4.safetensors"
DEFAULT_STEPS = 16
DEFAULT_VIDEO_FPS = 24
DEFAULT_VIDEO_FRAMES = 124  # ≈ 5s @24fps

# 主流 ratio -> H3 尺寸（32 对齐）
_RATIO_SIZE = {
    "16:9": (640, 352),
    "1:1": (512, 512),
    "9:16": (352, 640),
    "4:3": (512, 384),
    "3:4": (384, 512),
    "2:3": (384, 512),
    "3:2": (512, 384),
}


# ---------------------------------------------------------------------------
# 请求模型
# ---------------------------------------------------------------------------
class ImageGenRequest(BaseModel):
    model: Optional[str] = None
    prompt: str = Field(..., min_length=1)
    n: int = 1
    size: str = "1024x1024"
    quality: str = "high"
    style: Optional[str] = None
    response_format: str = "url"  # url | b64_json


class ContentItem(BaseModel):
    type: str  # text | image_url | video_url | audio_url
    text: Optional[str] = None
    image_url: Optional[dict[str, str]] = None
    video_url: Optional[dict[str, str]] = None
    audio_url: Optional[dict[str, str]] = None
    role: Optional[str] = None  # first_frame | last_frame | reference_image | ...


class VideoGenRequest(BaseModel):
    model: Optional[str] = None
    content: list[ContentItem] = Field(default_factory=list)
    duration: Optional[int] = None      # 秒
    frames: Optional[int] = None        # 帧数（优先于 duration）
    ratio: str = "16:9"                 # 16:9 | 1:1 | 9:16 | 4:3 | 3:4 | adaptive
    resolution: str = "480P"            # 480P | 720P | 1080P
    seed: int = -1
    generate_audio: bool = True
    watermark: bool = False


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------
def _parse_size(size: str | None) -> tuple[int, int]:
    if not size or size == "auto":
        return 1024, 1024
    m = re.match(r"^(\d+)[xX×](\d+)$", str(size))
    if not m:
        raise HTTPException(400, f"不支持的 size: {size}，应为 '宽x高' 如 1024x1024")
    return int(m.group(1)), int(m.group(2))


async def _fetch_bytes(url: str) -> bytes:
    """下载 http(s) URL 或解码 data: base64。"""
    if url.startswith("data:"):
        m = re.match(r"data:[^;]+;base64,(.+)", url, re.S)
        if not m:
            raise HTTPException(400, "无法解析 data: URL")
        return base64.b64decode(m.group(1))
    async with httpx.AsyncClient(timeout=90) as client:
        r = await client.get(url)
        r.raise_for_status()
        return r.content


async def _wait_task(tm, task_id: str, timeout: float = 300.0) -> TaskOut:
    """同步等待任务到终态（OpenAI 图片接口为同步语义）。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        t = tm.get(task_id)
        if t.status in ("succeeded", "failed"):
            return t
        await asyncio.sleep(1)
    raise HTTPException(504, f"任务等待超时（{task_id}）")


def _pick_backend(tpl: dict, st) -> str:
    backend, _ = probe_capability(
        tpl, st.primary_server, st.object_info or {},
        st.comfy_servers, st.object_infos or {},
    )
    return backend


def _ratio_size(ratio: str) -> tuple[int, int]:
    return _RATIO_SIZE.get(str(ratio).lower(), _RATIO_SIZE["16:9"])


def _frames_from(duration: Optional[int], frames: Optional[int]) -> int:
    if frames and frames > 0:
        return frames
    if duration and duration > 0:
        return max(8, duration * DEFAULT_VIDEO_FPS)
    return DEFAULT_VIDEO_FRAMES


def _seed(seed: int) -> int:
    return seed if seed and seed >= 0 else 0


# ---------------------------------------------------------------------------
# OpenAI Images 兼容（同步）
# ---------------------------------------------------------------------------
@router.post("/images/generations")
async def images_generations(request: Request, body: ImageGenRequest):
    st = request.app.state
    tpl = next((t for t in st.templates if t["type"] == "txt2img"), None)
    if not tpl:
        raise HTTPException(503, "文生图能力不可用")
    if body.n > 1:
        raise HTTPException(400, "当前版本仅支持 n=1")

    width, height = _parse_size(body.size)
    backend = _pick_backend(tpl, st)
    task = await st.task_manager.create("txt2img", {
        "prompt": body.prompt,
        "model": DEFAULT_IMAGE_MODEL,
        "width": width,
        "height": height,
        "steps": DEFAULT_STEPS,
        "seed": -1,
    }, backend)
    t = await _wait_task(st.task_manager, task.id)
    if t.status != "succeeded" or not t.result or not t.result.assets:
        raise HTTPException(502, t.error or "图片生成失败")

    asset = t.result.assets[0]
    base = str(request.base_url).rstrip("/")
    data: dict = {"revised_prompt": body.prompt}
    if body.response_format == "b64_json":
        async with httpx.AsyncClient(timeout=30) as client:
            b64 = await client.get(base + asset.url)
            data["b64_json"] = base64.b64encode(b64.content).decode()
    else:
        data["url"] = base + asset.url
    return {"created": int(time.time()), "data": [data]}


@router.post("/images/edits")
async def images_edits(
    request: Request,
    image: UploadFile = File(...),
    prompt: str = Form(...),
    model: Optional[str] = Form(None),
    n: int = Form(1),
    size: str = Form("1024x1024"),
    response_format: str = Form("url"),
):
    st = request.app.state
    tpl = next((t for t in st.templates if t["type"] == "img2img"), None)
    if not tpl:
        raise HTTPException(503, "图生图能力不可用")
    if n > 1:
        raise HTTPException(400, "当前版本仅支持 n=1")

    content = await image.read()
    width, height = _parse_size(size)
    backend = _pick_backend(tpl, st)
    task = await st.task_manager.create("img2img", {
        "prompt": prompt,
        "model": DEFAULT_IMAGE_MODEL,
        "width": width,
        "height": height,
        "denoise": 0.6,
        "steps": DEFAULT_STEPS,
        "seed": -1,
    }, backend, image_bytes=content)
    t = await _wait_task(st.task_manager, task.id)
    if t.status != "succeeded" or not t.result or not t.result.assets:
        raise HTTPException(502, t.error or "图片编辑失败")

    asset = t.result.assets[0]
    base = str(request.base_url).rstrip("/")
    data: dict = {"revised_prompt": prompt}
    if response_format == "b64_json":
        async with httpx.AsyncClient(timeout=30) as client:
            b64 = await client.get(base + asset.url)
            data["b64_json"] = base64.b64encode(b64.content).decode()
    else:
        data["url"] = base + asset.url
    return {"created": int(time.time()), "data": [data]}


# ---------------------------------------------------------------------------
# 主流视频生成兼容（异步，content[] 风格）
# ---------------------------------------------------------------------------
@router.post("/videos/generations")
async def videos_generations(request: Request, body: VideoGenRequest):
    """创建视频生成任务，返回 {id, task_id}。

    content[] 支持：
      {"type":"text","text":"..."}
      {"type":"image_url","image_url":{"url":"..."},"role":"first_frame"}（首帧 → 图生视频）
      video_url / audio_url 暂透传提示词语义（H3 本地版暂不消费）
    """
    st = request.app.state

    prompt = ""
    first_frame: Optional[bytes] = None
    for item in body.content:
        if item.type == "text" and item.text:
            prompt = item.text.strip()
        elif item.type == "image_url" and item.image_url and item.image_url.get("url"):
            if first_frame is None:  # 取首张图片作为首帧
                first_frame = await _fetch_bytes(item.image_url["url"])

    if not prompt and first_frame is None:
        raise HTTPException(400, "content 中需要至少一段文本提示词")

    width, height = _ratio_size(body.ratio)
    frames = _frames_from(body.duration, body.frames)
    seed = _seed(body.seed)

    if first_frame is not None:
        task_type = "img2video"
        tpl = next((t for t in st.templates if t["type"] == "img2video"), None)
        params = {
            "prompt": prompt, "width": width, "height": height,
            "video_frames": frames, "steps": 4, "fps": DEFAULT_VIDEO_FPS, "seed": seed,
        }
    else:
        task_type = "txt2video"
        tpl = next((t for t in st.templates if t["type"] == "txt2video"), None)
        params = {
            "prompt": prompt, "width": width, "height": height,
            "length": frames, "steps": 4, "fps": DEFAULT_VIDEO_FPS, "seed": seed,
        }
    if not tpl:
        raise HTTPException(503, f"{task_type} 能力不可用")

    backend = _pick_backend(tpl, st)
    task = await st.task_manager.create(
        task_type, params, backend, image_bytes=first_frame
    )
    return {
        "id": task.id,
        "task_id": task.id,
        "model": body.model or "MiniMax-H3",
        "status": "processing",
        "created": int(time.time()),
    }


@router.get("/videos/generations/{task_id}")
def videos_generation_status(task_id: str, request: Request):
    st = request.app.state
    t = st.task_manager.get(task_id)
    if not t:
        raise HTTPException(404, f"任务不存在: {task_id}")
    status = "processing" if t.status in ("pending", "running") else t.status
    out: dict[str, Any] = {
        "id": t.id,
        "task_id": t.id,
        "model": t.params.get("model", "MiniMax-H3"),
        "status": status,
        "created": int(t.created_at),
    }
    if t.status == "succeeded" and t.result and t.result.assets:
        asset = t.result.assets[0]
        out["content"] = {
            "type": asset.kind,
            "url": asset.url,
            "filename": asset.filename,
        }
    if t.status == "failed":
        out["error"] = t.error
    return out


# ---------------------------------------------------------------------------
# 模型发现（OpenAI 风格 + 详细分组）
# ---------------------------------------------------------------------------
# 模型类型 -> (类型标识, 中文标签, [(loader, 字段), ...])
_MODEL_TYPES = [
    ("checkpoints", "图片底模 Checkpoints（文生图 / 图生图）",
     [("CheckpointLoaderSimple", "ckpt_name"),
      ("ImageOnlyCheckpointLoader", "ckpt_name")]),
    ("diffusion_models", "扩散模型 Diffusion Models（视频 / 生成主模型）",
     [("UNETLoader", "unet_name")]),
    ("text_encoders", "文本编码器 Text Encoders",
     [("CLIPLoader", "clip_name"), ("ClipProjLoader", "clip_name")]),
    ("vae", "VAE 解码器",
     [("VAELoader", "vae_name")]),
    ("loras", "LoRA 模型",
     [("LoraLoaderModelOnly", "lora_name")]),
]


def _loader_options(object_info: dict | None, loader: str, field: str) -> list[str]:
    """从 object_info 提取某 loader 字段的可选模型列表。"""
    node = (object_info or {}).get(loader, {})
    spec = node.get("input", {}).get("required", {}).get(field)
    if isinstance(spec, list) and len(spec) >= 1:
        options = spec[0]
        if isinstance(options, list):
            if options and isinstance(options[0], (list, tuple)):
                options = options[0]
            return [str(o) for o in options]
    return []


def _template_uses_model(tpl: dict, model_id: str, loader: str, field: str) -> bool:
    """判断模型是否被某模板（能力）引用：options_source 或 requires_models 命中。"""
    for p in tpl.get("params", []):
        src = p.get("options_source")
        if src and src.get("loader") == loader and src.get("field") == field:
            return True
    for b in tpl.get("backends", {}).values():
        for req in b.get("requires_models", []):
            if req.get("loader") == loader and req.get("field") == field:
                m = req.get("match", [])
                if m and not any(k.lower() in model_id.lower() for k in m):
                    continue
                return True
    return False


def _build_model_registry(st) -> list[dict]:
    """汇总所有可达 ComfyUI 节点上的真实模型清单。

    每个条目: {id, type, nodes, capabilities}
    """
    nodes = getattr(st, "comfy_servers", {}) or {}
    infos = getattr(st, "object_infos", {}) or {}
    templates = getattr(st, "templates", []) or []
    registry: dict[str, dict] = {}

    for name, srv in nodes.items():
        if not getattr(srv, "reachable", False):
            continue
        info = infos.get(name, {}) or {}
        for mtype, _label, loaders in _MODEL_TYPES:
            for loader, field in loaders:
                for mid in _loader_options(info, loader, field):
                    entry = registry.setdefault(mid, {
                        "id": mid, "type": mtype,
                        "nodes": [], "capabilities": set(),
                    })
                    if name not in entry["nodes"]:
                        entry["nodes"].append(name)
                    for tpl in templates:
                        if _template_uses_model(tpl, mid, loader, field):
                            entry["capabilities"].add(tpl["type"])

    out = []
    for e in registry.values():
        e["capabilities"] = sorted(e["capabilities"])
        out.append(e)
    out.sort(key=lambda e: (e["type"], e["id"]))
    return out


@router.get("/models")
async def models_list(request: Request):
    """OpenAI 兼容模型列表（真实发现：来自 ComfyUI 节点的实际模型文件）。"""
    st = request.app.state
    registry = _build_model_registry(st)
    return {
        "object": "list",
        "data": [
            {
                "id": e["id"],
                "object": "model",
                "created": 0,
                "owned_by": e["nodes"][0] if e["nodes"] else "unknown",
                "capabilities": e["capabilities"],
                "type": e["type"],
            }
            for e in registry
        ],
    }


@router.get("/models/discover")
async def models_discover(request: Request):
    """详细模型发现：按类型 / 按能力分组，含节点来源与模型总数。"""
    st = request.app.state
    nodes = getattr(st, "comfy_servers", {}) or {}
    registry = _build_model_registry(st)

    by_type = {k: {"label": label, "models": []} for k, label, _ in _MODEL_TYPES}
    for e in registry:
        by_type[e["type"]]["models"].append(e)

    caps = {}
    for tpl in st.templates:
        caps[tpl["type"]] = {
            "label": tpl.get("label", tpl["type"]),
            "models": [e["id"] for e in registry if tpl["type"] in e["capabilities"]],
        }

    return {
        "total": len(registry),
        "nodes": [
            {"name": n, "reachable": s.reachable, "url": s.base_url}
            for n, s in nodes.items()
        ],
        "types": by_type,
        "capabilities": caps,
    }


@router.get("/models/{model_id}")
async def model_detail(model_id: str, request: Request):
    """OpenAI 风格单模型查询（模型 id 含反斜杠时请 URL 编码）。"""
    registry = _build_model_registry(request.app.state)
    for e in registry:
        if e["id"] == model_id:
            return {
                "id": e["id"], "object": "model", "created": 0,
                "owned_by": e["nodes"], "capabilities": e["capabilities"],
                "type": e["type"],
            }
    raise HTTPException(404, f"模型不存在: {model_id}")

