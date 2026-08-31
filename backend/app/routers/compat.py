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
import struct
import time
from pathlib import Path
from typing import Any, Optional

import httpx
from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel, Field

from ..config import settings
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

# quality 档位 -> 采样步数
_QUALITY_STEPS = {"low": 12, "medium": 20, "high": 28}

# 风格预设 -> (模型, 风格词, 采样器, CFG)
# 模型名取自本机真实健康 checkpoint，风格词注入到提示词末尾
_STYLE_PRESETS: dict[str, dict[str, Any]] = {
    "auto": {
        "model": DEFAULT_IMAGE_MODEL,
        "words": "",
        "sampler": "dpmpp_2m", "cfg": 7.0,
    },
    "photorealistic": {
        "model": "majicMIX realistic 逼真风格\\majicmixRealistic_v4.safetensors",
        "words": "photorealistic, 8k uhd, highly detailed skin texture, natural skin pores, "
                "85mm lens, soft studio lighting, sharp focus, professional photography",
        "sampler": "dpmpp_2m", "cfg": 7.0,
    },
    "cinematic": {
        "model": "majicMIX realistic 逼真风格\\majicmixRealistic_v4.safetensors",
        "words": "cinematic lighting, dramatic atmosphere, film grain, anamorphic lens, "
                "high contrast, shallow depth of field",
        "sampler": "dpmpp_2m", "cfg": 7.5,
    },
    "anime": {
        "model": "AWPainting 1.4\\AWPainting_v1.4.safetensors",
        "words": "anime style, cel shading, clean lineart, vibrant colors, "
                "high quality anime illustration",
        "sampler": "euler", "cfg": 7.0,
    },
    "watercolor": {
        "model": DEFAULT_IMAGE_MODEL,
        "words": "watercolor painting, soft brush strokes, paper texture, delicate washes, "
                "artistic, gentle colors",
        "sampler": "dpmpp_2m", "cfg": 7.0,
    },
    "ink": {
        "model": "墨幽\\MoyouArtificial_v10502g.safetensors",
        "words": "chinese ink wash painting, sumi-e, minimalist, elegant brush strokes, "
                "monochrome with subtle ink texture",
        "sampler": "dpmpp_2m", "cfg": 6.5,
    },
    "3d": {
        "model": DEFAULT_IMAGE_MODEL,
        "words": "3d render, octane render, soft global illumination, subsurface scattering, "
                "high quality 3d cg, clay render",
        "sampler": "dpmpp_2m", "cfg": 7.0,
    },
    "fantasy": {
        "model": "绪儿-红蓝幻想大模型\\绪儿-红蓝幻想大模型.safetensors",
        "words": "fantasy concept art, epic composition, magical atmosphere, "
                "intricate details, dynamic lighting",
        "sampler": "euler", "cfg": 7.5,
    },
    "pastel": {
        "model": "pastelMixStylizedAnime\\pastelMixStylizedAnime_pastelMixFull.safetensors",
        "words": "pastel colors, soft gentle tones, kawaii, dreamy atmosphere, "
                "smooth shading, cute",
        "sampler": "euler", "cfg": 7.0,
    },
}
_STYLE_ALIAS = {"realistic": "photorealistic"}


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


def _resolve_image_model(st, requested: Optional[str], mtype: str = "checkpoints") -> str:
    """校验并返回图片生成主模型。

    requested 若为真实存在且健康（health=ok）的 checkpoint 则使用，否则回退默认底模。
    """
    if requested:
        registry = _build_model_registry(st)
        for e in registry:
            if (e["role"] == "main" and e["type"] == mtype
                    and e["id"] == requested and e["health"] == "ok"):
                return requested
    return DEFAULT_IMAGE_MODEL


def _apply_style(
    style: Optional[str], prompt: str, requested_model: Optional[str], st
) -> tuple[str, str, str, float]:
    """按风格预设解析出 (最终提示词, 模型, 采样器, CFG)。

    显式 model 优先于风格预设模型；风格词追加到提示词末尾。
    """
    key = (style or "auto").strip().lower()
    key = _STYLE_ALIAS.get(key, key)
    preset = _STYLE_PRESETS.get(key)
    if preset is None:
        raise HTTPException(
            400,
            f"不支持的 style: {style}，可选: {', '.join(sorted(set(_STYLE_PRESETS) | set(_STYLE_ALIAS)))}",
        )
    model = _resolve_image_model(st, requested_model) if requested_model else preset["model"]
    words = preset.get("words", "")
    final = f"{prompt}, {words}" if words else prompt
    return final, model, preset["sampler"], preset["cfg"]


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

    final_prompt, model, sampler, cfg = _apply_style(
        body.style, body.prompt, body.model, st
    )
    steps = _QUALITY_STEPS.get((body.quality or "high").lower(), 28)
    width, height = _parse_size(body.size)
    backend = _pick_backend(tpl, st)
    task = await st.task_manager.create("txt2img", {
        "prompt": final_prompt,
        "model": model,
        "width": width,
        "height": height,
        "steps": steps,
        "cfg": cfg,
        "sampler": sampler,
        "seed": -1,
    }, backend)
    t = await _wait_task(st.task_manager, task.id)
    if t.status != "succeeded" or not t.result or not t.result.assets:
        raise HTTPException(502, t.error or "图片生成失败")

    asset = t.result.assets[0]
    base = str(request.base_url).rstrip("/")
    data: dict = {"revised_prompt": final_prompt}
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
    style: Optional[str] = Form(None),
    quality: str = Form("high"),
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

    final_prompt, model, sampler, cfg = _apply_style(
        style, prompt, model, st
    )
    steps = _QUALITY_STEPS.get((quality or "high").lower(), 28)
    content = await image.read()
    width, height = _parse_size(size)
    backend = _pick_backend(tpl, st)
    task = await st.task_manager.create("img2img", {
        "prompt": final_prompt,
        "model": model,
        "width": width,
        "height": height,
        "denoise": 0.6,
        "steps": steps,
        "cfg": cfg,
        "sampler": sampler,
        "seed": -1,
    }, backend, image_bytes=content)
    t = await _wait_task(st.task_manager, task.id)
    if t.status != "succeeded" or not t.result or not t.result.assets:
        raise HTTPException(502, t.error or "图片编辑失败")

    asset = t.result.assets[0]
    base = str(request.base_url).rstrip("/")
    data: dict = {"revised_prompt": final_prompt}
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
# 模型类型 -> (类型标识, 中文标签, role, [(loader, 字段), ...])
#   role=main      生成主模型（可作为生成接口的 model 参数）
#   role=component 工作流内部组件（VAE / 文本编码器 / LoRA，无需单独配置）
_MODEL_TYPES = [
    ("checkpoints", "图片底模 Checkpoints（文生图 / 图生图）", "main",
     [("CheckpointLoaderSimple", "ckpt_name"),
      ("ImageOnlyCheckpointLoader", "ckpt_name")]),
    ("diffusion_models", "扩散模型 Diffusion Models（视频 / 生成主模型）", "main",
     [("UNETLoader", "unet_name")]),
    ("text_encoders", "文本编码器 Text Encoders", "component",
     [("CLIPLoader", "clip_name"), ("ClipProjLoader", "clip_name")]),
    ("vae", "VAE 解码器", "component",
     [("VAELoader", "vae_name")]),
    ("loras", "LoRA 模型", "component",
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


# 模型类型 -> 磁盘子目录（本地文件健康校验用）
_MODEL_SUBDIR = {
    "checkpoints": "checkpoints",      # 兼容 sd-webui 的 Stable-diffusion
    "diffusion_models": "diffusion_models",
    "text_encoders": "text_encoders",
    "vae": "vae",
    "loras": "loras",
}
_HEADER_LEN_MAX = 16 * 1024 * 1024  # safetensors header 合理上限


def _check_model_health(mtype: str, name: str) -> str:
    """本地模型文件健康校验：读取 safetensors header 长度声明判断是否损坏。

    返回 ok（可用）/ corrupt（损坏）/ missing（未找到文件）/ unknown（无法校验）。
    """
    if not getattr(settings, "comfy_models_dir", ""):
        return "unknown"
    subdirs = [_MODEL_SUBDIR.get(mtype, "")]
    if mtype == "checkpoints":
        subdirs = ["checkpoints", "Stable-diffusion"]
    for base in settings.comfy_models_dir.split(";"):
        base = base.strip()
        if not base:
            continue
        for sd in subdirs:
            p = Path(base) / sd / name
            try:
                if not (p.is_file() and p.stat().st_size > 0):
                    continue
                with open(p, "rb") as f:
                    head = f.read(8)
                if len(head) < 8:
                    return "corrupt"
                n = struct.unpack("<Q", head)[0]
                return "ok" if 0 < n < _HEADER_LEN_MAX else "corrupt"
            except OSError:
                continue
    return "unknown"


def _build_model_registry(st) -> list[dict]:
    """汇总所有可达 ComfyUI 节点上的真实模型清单。

    每个条目: {id, type, role, nodes, capabilities, health}
    role: main（生成主模型）/ component（VAE·文本编码器·LoRA 等组件）
    health: ok / corrupt / missing / unknown（本地文件校验）
    """
    nodes = getattr(st, "comfy_servers", {}) or {}
    infos = getattr(st, "object_infos", {}) or {}
    templates = getattr(st, "templates", []) or []
    type_role = {t[0]: t[2] for t in _MODEL_TYPES}
    registry: dict[str, dict] = {}

    for name, srv in nodes.items():
        if not getattr(srv, "reachable", False):
            continue
        info = infos.get(name, {}) or {}
        for mtype, _label, _role, loaders in _MODEL_TYPES:
            for loader, field in loaders:
                for mid in _loader_options(info, loader, field):
                    entry = registry.setdefault(mid, {
                        "id": mid, "type": mtype, "role": type_role.get(mtype, "component"),
                        "nodes": [], "capabilities": set(), "health": "",
                    })
                    if name not in entry["nodes"]:
                        entry["nodes"].append(name)
                    for tpl in templates:
                        if _template_uses_model(tpl, mid, loader, field):
                            entry["capabilities"].add(tpl["type"])

    out = []
    for e in registry.values():
        e["capabilities"] = sorted(e["capabilities"])
        e["health"] = _check_model_health(e["type"], e["id"])
        out.append(e)
    # 主模型在前，healthy 在前
    out.sort(key=lambda e: (e["role"] != "main", e["health"] != "ok", e["type"], e["id"]))
    return out


@router.get("/models")
async def models_list(
    request: Request,
    role: str = "main",          # main(默认，仅生成主模型) | component | all
    type: str | None = None,     # 按模型类型过滤: checkpoints / diffusion_models / ...
    capability: str | None = None,  # 按能力过滤: txt2img / img2img / txt2video / img2video
):
    """OpenAI 兼容模型列表。

    默认只返回生成主模型（图片 checkpoint + 视频 diffusion），
    VAE / 文本编码器 / LoRA 等组件默认不列出（可通过 ?role=all 查看）。
    """
    st = request.app.state
    registry = _build_model_registry(st)
    out = []
    for e in registry:
        if role != "all" and e["role"] != role:
            continue
        if type and e["type"] != type:
            continue
        if capability and capability not in e["capabilities"]:
            continue
        out.append({
            "id": e["id"],
            "object": "model",
            "created": 0,
            "owned_by": e["nodes"][0] if e["nodes"] else "unknown",
            "capabilities": e["capabilities"],
            "type": e["type"],
            "role": e["role"],
            "health": e["health"],
        })
    return {"object": "list", "data": out}


@router.get("/models/discover")
async def models_discover(request: Request):
    """详细模型发现：按类型 / 按能力分组，含节点来源与模型总数。"""
    st = request.app.state
    nodes = getattr(st, "comfy_servers", {}) or {}
    registry = _build_model_registry(st)

    by_type = {k: {"label": label, "role": role, "models": []}
               for k, label, role, _ in _MODEL_TYPES}
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
                "type": e["type"], "role": e["role"], "health": e["health"],
            }
    raise HTTPException(404, f"模型不存在: {model_id}")

