"""任务执行器：按后端分派到 ComfyUI 真实执行，或降级为模拟生成。"""
from __future__ import annotations

import asyncio
import io
import os
import random
import time
import uuid
from typing import Any, Optional

from ..config import settings
from ..schemas import AssetInfo, TaskResult
from .comfy import ComfyServer
from .workflows import fill_workflow

_UPLOAD_PREFIX = "comfy-service-upload"
_MOCK_MARK = "【模拟结果】"


# ---------------------------------------------------------------------------
# Mock 生成：无本地模型 / 无云端密钥时的体验兜底
# ---------------------------------------------------------------------------
class MockGenerator:
    """用 Pillow 生成带说明文字的占位资源，保证前端链路可完整体验。"""

    def __init__(self, assets_dir):
        self.assets_dir = assets_dir

    async def generate(
        self, kind: str, params: dict[str, Any]
    ) -> TaskResult:
        loop = asyncio.get_event_loop()
        if kind == "image":
            path, note = await loop.run_in_executor(
                None, self._make_image, params
            )
        else:
            path, note = await loop.run_in_executor(
                None, self._make_gif, params
            )
        rel = os.path.basename(path)
        url = f"/api/v1/assets/mock/{rel}"
        return TaskResult(
            assets=[AssetInfo(
                kind=kind, url=url, filename=rel, note=note,
            )],
            info={"backend": "mock", "note": note},
        )

    def _make_image(self, params: dict[str, Any]) -> tuple[str, str]:
        from PIL import Image, ImageDraw, ImageFont

        prompt = str(params.get("prompt") or "（未填写提示词）")
        w = int(params.get("width", 512) or 512)
        h = int(params.get("height", 512) or 512)
        w = max(64, min(w, 1536))
        h = max(64, min(h, 1536))

        img = Image.new("RGB", (w, h))
        d = ImageDraw.Draw(img)
        seed = int(params.get("seed") or 0)
        rnd = random.Random(seed if seed >= 0 else None)
        base = tuple(rnd.randint(30, 220) for _ in range(3))
        accent = tuple(max(0, min(255, c + rnd.randint(-80, 80))) for c in base)
        # 对角渐变
        for y in range(h):
            t = y / max(1, h - 1)
            color = tuple(int(base[i] * (1 - t) + accent[i] * t) for i in range(3))
            d.line([(0, y), (w, y)], fill=color)
        # 网格装饰
        for x in range(0, w, max(32, w // 16)):
            d.line([(x, 0), (x, h)], fill=(255, 255, 255, 0), width=1)
        d.rectangle([x for x in [0, 0, w - 1, h - 1]], outline=(255, 255, 255))

        try:
            font = ImageFont.load_default()
        except Exception:  # noqa: BLE001
            font = None

        lines = [
            _MOCK_MARK,
            "后端未就绪（本地模型缺失或云端密钥未配置）",
            f"类型: {params.get('_type', 'image')}  分辨率: {w}x{h}",
            f"提示词: {prompt[:80]}",
        ]
        y = 24
        for ln in lines:
            d.text((24, y), ln, fill=(255, 255, 255), font=font)
            y += 26

        name = f"mock_img_{int(time.time())}_{uuid.uuid4().hex[:6]}.png"
        full = os.path.join(self.assets_dir, name)
        img.save(full)
        return full, "模拟图片（后端未就绪）"

    def _make_gif(self, params: dict[str, Any]) -> tuple[str, str]:
        from PIL import Image, ImageDraw, ImageFont

        prompt = str(params.get("prompt") or "（未填写提示词）")
        w = int(params.get("width", 480) or 480)
        h = int(params.get("height", 320) or 320)
        frames_n = min(max(int(params.get("video_frames") or params.get("length") or 12), 4), 24)
        frames = []
        for i in range(frames_n):
            t = i / max(1, frames_n - 1)
            img = Image.new("RGB", (w, h))
            d = ImageDraw.Draw(img)
            r = int(80 + t * 120)
            g = int(60 + (1 - t) * 100)
            b = int(120 + t * 60)
            for y in range(h):
                d.line([(0, y), (w, y)], fill=(r - int(t * 30), g, b))
            # 移动的圆点，模拟"动态"
            cx = int(w * (0.2 + 0.6 * t))
            cy = int(h * 0.5)
            d.ellipse([cx - 24, cy - 24, cx + 24, cy + 24], fill=(255, 255, 255))
            frames.append(img)
        name = f"mock_vid_{int(time.time())}_{uuid.uuid4().hex[:6]}.gif"
        full = os.path.join(self.assets_dir, name)
        frames[0].save(
            full, save_all=True, append_images=frames[1:], duration=120, loop=0,
        )
        return full, "模拟视频 GIF（后端未就绪）"


# ---------------------------------------------------------------------------
# ComfyUI 真实执行
# ---------------------------------------------------------------------------
class ComfyExecutor:
    """按后端名分派到对应 ComfyUI 节点执行。

    servers: 后端名 -> ComfyServer 的映射（如 comfy_local=8188 图片、comfy_h3=8189 视频）。
    """

    def __init__(self, servers: dict[str, ComfyServer], primary: ComfyServer):
        self.servers = servers
        self.primary = primary

    def _server_for(self, backend: str) -> ComfyServer:
        return self.servers.get(backend, self.primary)

    async def run(
        self,
        template: dict[str, Any],
        backend: str,
        params: dict[str, Any],
        image_bytes: Optional[bytes] = None,
    ) -> TaskResult:
        server = self._server_for(backend)

        # 1) 需要图片时先上传到对应节点
        image_filename = ""
        if image_bytes:
            image_filename = (
                f"{_UPLOAD_PREFIX}_{uuid.uuid4().hex[:8]}.png"
            )
            await server.upload_image(image_filename, image_bytes)

        # 2) 填充 workflow 并提交
        workflow = fill_workflow(
            template, backend, params, image_filename=image_filename
        )
        prompt_id = await server.submit(workflow)
        cfg = template["backends"][backend]
        output_node = str(cfg.get("output_node", ""))
        output_kind = cfg.get("output_kind", "image")

        # 3) 轮询执行结果
        deadline = time.time() + 1800  # 视频任务可能很久
        while time.time() < deadline:
            await asyncio.sleep(settings.poll_interval)
            hist = await server.history(prompt_id)
            if prompt_id not in hist:
                continue
            entry = hist[prompt_id]
            status = entry.get("status", {})
            if status.get("status_str") == "error":
                msg = self._extract_error(entry)
                raise RuntimeError(msg)
            if status.get("completed"):
                assets, extra = self._extract_outputs(
                    entry, output_node, output_kind, node=server.name
                )
                if not assets:
                    raise RuntimeError("ComfyUI 执行完成但未提取到结果资源")
                return TaskResult(
                    assets=assets,
                    info={"backend": backend, "prompt_id": prompt_id, **extra},
                )
        raise RuntimeError("等待 ComfyUI 结果超时")

    @staticmethod
    def _extract_error(entry: dict[str, Any]) -> str:
        for m in entry.get("status", {}).get("messages", []):
            if m[0] == "execution_error":
                return str(m[1].get("exception_message", m[1]))[:500]
        return "ComfyUI 执行出错"

    @staticmethod
    def _extract_outputs(
        entry: dict[str, Any], output_node: str, kind: str, node: str = ""
    ) -> tuple[list[AssetInfo], dict[str, Any]]:
        assets: list[AssetInfo] = []
        outputs = entry.get("outputs", {})
        node_outs = outputs.get(output_node, {})
        # 兼容 images / gifs / videos 三种输出字段
        for field in ("images", "gifs", "videos"):
            for item in node_outs.get(field, []):
                filename = item.get("filename", "")
                subfolder = item.get("subfolder", "")
                img_type = item.get("type", "output")
                url = (
                    f"/api/v1/assets/proxy?filename={filename}"
                    f"&subfolder={subfolder}&type={img_type}"
                )
                if node:
                    url += f"&node={node}"
                assets.append(AssetInfo(
                    kind=kind,
                    url=url,
                    filename=filename,
                    note="",
                ))
        return assets, {"output_node": output_node}
