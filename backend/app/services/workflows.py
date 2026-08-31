"""Workflow 模板：加载、参数填充、能力探测（本地/云端/模拟 三级降级）。"""
from __future__ import annotations

import json
import random
import re
from typing import Any, Optional

from ..config import settings
from ..schemas import CapabilityOut, CapabilityParam
from .comfy import ComfyServer

_TEMPLATE_DIR = settings.templates_dir
_PLACEHOLDER = re.compile(r"\$([a-zA-Z_][a-zA-Z0-9_]*)")

# 与 config.Settings 字段名对齐的密钥探测
_KEY_FIELDS = ("wan_api_key",)

# 本地 checkpoint 试跑校准结果（同机模式）
_local_calib: dict[str, Any] = {"ok": True, "reason": ""}


def set_calibration(ok: bool, reason: str = "") -> None:
    _local_calib["ok"] = ok
    _local_calib["reason"] = reason


def get_calibration() -> dict[str, Any]:
    return dict(_local_calib)


def load_templates() -> list[dict[str, Any]]:
    """读取 templates/*.json 全部模板。"""
    templates = []
    for f in sorted(_TEMPLATE_DIR.glob("*.json")):
        try:
            with open(f, encoding="utf-8") as fh:
                templates.append(json.load(fh))
        except Exception as e:  # noqa: BLE001
            print(f"[workflows] 跳过模板 {f.name}: {e}")
    return templates


def _loader_options(
    object_info: dict[str, Any], loader: str, field: str
) -> list[str]:
    """从 object_info 中提取某个 loader 字段的可选模型列表。"""
    node = object_info.get(loader, {})
    spec = node.get("input", {}).get("required", {}).get(field)
    if isinstance(spec, list) and len(spec) >= 1:
        options = spec[0]
        if isinstance(options, list):
            # 兼容两种形态：[["a","b"]] 或 ["a","b"]
            if options and isinstance(options[0], (list, tuple)):
                options = options[0]
            return [str(o) for o in options]
    return []


def _models_present(
    server: ComfyServer,
    object_info: dict[str, Any],
    requires_models: list[dict[str, str]],
) -> tuple[bool, str]:
    """检查模板所需模型是否真实存在。

    优先级：
    1. 配置了 COMFY_MODELS_DIR（同机本地节点）→ 直接校验磁盘文件真实存在；
    2. 否则依赖 object_info 的 loader 候选列表（乐观探测，远程节点场景）。
    """
    missing: list[str] = []

    for req in requires_models:
        opts = _loader_options(object_info, req["loader"], req["field"])
        if not opts:
            missing.append(f"{req['loader']}.{req['field']}")
            continue
        # 关键词过滤：模板可声明所需模型族（如 SVD 视频模型）
        match_kw = req.get("match", [])
        if match_kw:
            opts = [
                o for o in opts
                if any(k.lower() in o.lower() for k in match_kw)
            ]
            if not opts:
                missing.append(
                    f"{req['loader']}.{req['field']}（无 {match_kw} 类模型）"
                )
                continue
        # 同机模式：真实文件校验
        if settings.comfy_models_dir:
            subdir = _MODEL_SUBDIR.get(req["loader"], "")
            if not _any_model_file_exists(subdir, opts):
                missing.append(
                    f"{req['loader']}.{req['field']}（目录无真实模型文件）"
                )

    if missing:
        return False, "缺少模型: " + ", ".join(missing)
    return True, ""


_MODEL_SUBDIR = {
    "CheckpointLoaderSimple": "checkpoints",
    "ImageOnlyCheckpointLoader": "checkpoints",
    "UNETLoader": "diffusion_models",
    "CLIPLoader": "text_encoders",
    "ClipProjLoader": "text_encoders",
    "VAELoader": "vae",
    "LoraLoaderModelOnly": "loras",
}


def _any_model_file_exists(subdir: str, names: list[str]) -> bool:
    """在配置的模型目录中查找任一候选文件真实存在。

    兼容 ComfyUI(checkpoints) 与 sd-webui(Stable-diffusion) 两种目录布局。
    """
    from pathlib import Path

    subdirs = [subdir]
    if subdir == "checkpoints":
        subdirs = ["checkpoints", "Stable-diffusion"]

    for base in settings.comfy_models_dir.split(";"):
        base = base.strip()
        if not base:
            continue
        base_path = Path(base)
        for sd in subdirs:
            for name in names:
                # 兼容嵌套路径（如 "AWPainting 1.4\\xxx.safetensors"）
                candidate = base_path / sd / name
                try:
                    if candidate.is_file() and candidate.stat().st_size > 0:
                        return True
                except OSError:
                    continue
    return False


async def calibrate_image_backend(
    server: ComfyServer, object_info: dict[str, Any]
) -> tuple[bool, str]:
    """对本地 checkpoint 做一次真实试跑校准。

    用 txt2img 模板 + 首个候选模型 + 最小参数提交，验证模型可真实加载。
    返回 (可用, 说明)。
    """
    tpl = None
    for t in load_templates():
        if t["type"] == "txt2img":
            tpl = t
            break
    if not tpl:
        return False, "未找到 txt2img 模板"

    local_cfg = tpl.get("backends", {}).get("comfy_local")
    if not local_cfg:
        return False, "txt2img 无本地后端"

    # 首个候选模型
    model = ""
    for req in local_cfg.get("requires_models", []):
        opts = _loader_options(object_info, req["loader"], req["field"])
        if opts:
            model = opts[0]
            break
    if not model:
        return False, "无可用 checkpoint 候选"

    params = {
        "prompt": "calibration",
        "negative_prompt": "",
        "model": model,
        "width": 256,
        "height": 256,
        "steps": 3,
        "cfg": 4.0,
        "sampler": "euler",
        "seed": 1,
        "batch_size": 1,
    }
    try:
        workflow = fill_workflow(tpl, "comfy_local", params)
        prompt_id = await server.submit(workflow)
        # 轮询较短时间（校准用，最多 ~90s）
        import asyncio
        import time

        deadline = time.time() + 90
        while time.time() < deadline:
            await asyncio.sleep(1.5)
            hist = await server.history(prompt_id)
            if prompt_id not in hist:
                continue
            entry = hist[prompt_id]
            status = entry.get("status", {})
            if status.get("status_str") == "error":
                for m in status.get("messages", []):
                    if m[0] == "execution_error":
                        msg = str(m[1].get("exception_message", ""))[:200]
                        return False, f"试跑失败: {msg}"
                return False, "试跑失败（未知错误）"
            if status.get("completed"):
                return True, f"本地 checkpoint 可用（{model}）"
        return False, "试跑超时"
    except Exception as e:  # noqa: BLE001
        return False, f"试跑异常: {str(e)[:200]}"


def _key_configured(requires_key: str) -> bool:
    value = getattr(settings, requires_key, "") or ""
    return bool(value)


def probe_capability(
    template: dict[str, Any],
    server: ComfyServer,
    object_info: dict[str, Any],
    servers: Optional[dict[str, ComfyServer]] = None,
    object_infos: Optional[dict[str, dict[str, Any]]] = None,
) -> tuple[str, str]:
    """返回 (激活后端, 说明)。优先级: comfy_local > comfy_h3 > comfy_api > mock。

    servers: 后端名 -> ComfyServer 映射（多节点）。
    object_infos: 后端名 -> object_info 映射（不同节点承载不同节点集）。
    """
    if settings.force_mock:
        return "mock", "已强制启用模拟模式（COMFY_FORCE_MOCK=true）"

    servers = servers or {}
    object_infos = object_infos or {}
    backends = template.get("backends", {})
    for name in template.get("backend_order", []):
        cfg = backends.get(name)
        if not cfg:
            continue

        if name in ("comfy_local", "comfy_h3"):
            node = servers.get(name, server)
            if not node.reachable:
                continue
            node_info = object_infos.get(name, object_info)
            # 图片类本地节点：同机模式试跑校准失败则跳过
            if (
                name == "comfy_local"
                and settings.comfy_models_dir
                and not _local_calib["ok"]
            ):
                continue
            # 节点检查
            requires_nodes = cfg.get("requires_nodes", [])
            missing_nodes = [
                n for n in requires_nodes if n not in node_info
            ]
            if missing_nodes:
                continue
            # 模型检查
            ok, msg = _models_present(
                node, node_info, cfg.get("requires_models", [])
            )
            if not ok:
                continue
            label = "本地 GPU 推理（ComfyUI·H3 视频节点）" if name == "comfy_h3" \
                else "本地 GPU 推理（ComfyUI）"
            return name, label

        if name == "comfy_api":
            node_type = cfg.get("node_type")
            if not node_type or node_type not in object_info:
                continue
            key = cfg.get("requires_key")
            if key and not _key_configured(key):
                continue
            return "comfy_api", f"云端 API 节点（{node_type}）"

    return "mock", "当前无可用本地模型/云端密钥，使用模拟模式（放置模型或配置密钥后自动切换）"


def build_capability(
    template: dict[str, Any],
    server: ComfyServer,
    object_info: dict[str, Any],
    servers: Optional[dict[str, ComfyServer]] = None,
    object_infos: Optional[dict[str, dict[str, Any]]] = None,
) -> CapabilityOut:
    """根据模板 + 实时资源，构建能力视图（含动态参数选项）。"""
    servers = servers or {}
    object_infos = object_infos or {}
    backend, reason = probe_capability(
        template, server, object_info, servers, object_infos
    )

    # 能力激活对应的节点及其 object_info（用于下拉选项注入 / 默认参数）
    active_node = servers.get(backend, server) if backend in servers else server
    active_info = object_infos.get(backend, object_info)

    params: list[CapabilityParam] = []
    for p in template.get("params", []):
        item = CapabilityParam(
            name=p["name"],
            label=p.get("label", p["name"]),
            type=p.get("type", "text"),
            default=p.get("default"),
            required=p.get("required", False),
            description=p.get("description", ""),
            min=p.get("min"),
            max=p.get("max"),
        )
        # 模型下拉选项动态注入
        if (
            p.get("options_source")
            and active_node.reachable
            and active_info
        ):
            src = p["options_source"]
            item.options = _loader_options(
                active_info, src["loader"], src["field"]
            )
            if not item.default and item.options:
                item.default = item.options[0]
        elif p.get("options"):
            item.options = p["options"]
        params.append(item)

    available = [b for b in template.get("backend_order", []) if b in template.get("backends", {})]
    if backend == "mock":
        available.append("mock")
    backend_status = "ready" if backend != "mock" else "mock"

    return CapabilityOut(
        type=template["type"],
        label=template.get("label", template["type"]),
        description=template.get("description", ""),
        backend=backend,
        backend_status=backend_status,
        backends=available,
        reason=reason,
        params=params,
        accepts_image=template.get("accepts_image", False),
    )


def fill_workflow(
    template: dict[str, Any],
    backend: str,
    params: dict[str, Any],
    image_filename: str = "",
) -> dict[str, Any]:
    """用业务参数填充模板 workflow，返回可提交给 ComfyUI 的 dict。

    参数占位符 $name 出现在字符串中时：
      - 参数类型为 text/select -> 保留为字符串
      - 参数类型为 int/float/bool -> 替换为 JSON 字面量
    image 参数自动替换为上传后的文件名。
    """
    backend_cfg = template["backends"][backend]
    workflow = json.loads(json.dumps(backend_cfg["workflow"]))  # 深拷贝
    param_specs = {p["name"]: p for p in template.get("params", [])}
    actual = dict(params)

    # 用模板默认值补齐未传参数，避免占位符无法替换
    for p in template.get("params", []):
        if p["name"] not in actual and p.get("default") is not None:
            actual[p["name"]] = p["default"]

    # 主流语义：seed 为负数（如 -1）表示随机
    seed_val = actual.get("seed")
    if isinstance(seed_val, (int, float)) and seed_val < 0:
        actual["seed"] = random.randint(0, 2**31 - 1)

    # 上传的图片：模板中的 $image 用真实文件名
    if image_filename:
        actual["image"] = image_filename

    def conv(value: str) -> Any:
        m = _PLACEHOLDER.fullmatch(value)
        if not m:
            return value
        name = m.group(1)
        if name not in actual:
            return value
        spec = param_specs.get(name, {})
        ptype = spec.get("type", "text")
        raw = actual[name]
        if ptype in ("int", "float"):
            try:
                return int(raw) if ptype == "int" else float(raw)
            except (TypeError, ValueError):
                return raw
        if ptype == "bool":
            return bool(raw)
        return str(raw)

    def walk(node: Any) -> Any:
        if isinstance(node, dict):
            return {k: walk(v) for k, v in node.items()}
        if isinstance(node, list):
            return [walk(v) for v in node]
        if isinstance(node, str):
            # 整值占位符 -> 类型转换；嵌入占位符 -> 简单字符串替换
            m = _PLACEHOLDER.fullmatch(node)
            if m:
                return conv(node)
            if "$" in node:
                out = node
                for pm in _PLACEHOLDER.finditer(node):
                    name = pm.group(1)
                    if name in actual:
                        out = out.replace(pm.group(0), str(actual[name]))
                return out
            return node
        return node

    return walk(workflow)
