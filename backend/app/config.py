"""全局配置：支持环境变量 / .env 文件覆盖。"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent  # backend/
ASSETS_DIR = BASE_DIR / "assets"
TEMPLATES_DIR = BASE_DIR / "app" / "templates"

# 默认 .env 位置
load_dotenv(BASE_DIR / ".env")


class Settings:
    # ---- 服务 ----
    app_name: str = "Comfy Service"
    host: str = os.getenv("COMFY_SERVICE_HOST", "0.0.0.0")
    port: int = int(os.getenv("COMFY_SERVICE_PORT", "8000"))
    api_prefix: str = "/api/v1"

    # ---- ComfyUI 节点配置 ----
    # 可配置多个节点，用逗号分隔；每个节点可选前缀格式：name=url
    # 例：comfy_local=http://127.0.0.1:8188,comfy_h3=http://127.0.0.1:8189
    comfy_servers_raw: str = os.getenv(
        "COMFY_SERVERS",
        "comfy_local=http://127.0.0.1:8188,comfy_h3=http://127.0.0.1:8189",
    )

    # ---- 任务 ----
    task_ttl_seconds: int = int(os.getenv("COMFY_TASK_TTL", str(24 * 3600)))
    poll_interval: float = float(os.getenv("COMFY_POLL_INTERVAL", "1.0"))
    comfy_timeout: float = float(os.getenv("COMFY_TIMEOUT", "30"))

    # ---- 模拟模式 ----
    # force_mock=true 时强制所有能力走 mock（便于无模型环境演示）
    force_mock: bool = os.getenv("COMFY_FORCE_MOCK", "").lower() in (
        "1", "true", "yes",
    )

    @property
    def assets_dir(self) -> Path:
        return BASE_DIR / "assets"

    @property
    def templates_dir(self) -> Path:
        return BASE_DIR / "app" / "templates"

    @property
    def comfy_servers(self) -> dict[str, str]:
        """解析为 {name: base_url}。"""
        out: dict[str, str] = {}
        for item in self.comfy_servers_raw.split(","):
            item = item.strip()
            if not item:
                continue
            if "=" in item:
                name, url = item.split("=", 1)
                out[name.strip()] = url.strip().rstrip("/")
            else:
                out[f"node{len(out) + 1}"] = item.rstrip("/")
        return out

    # ---- 云端 API 节点密钥（供 comfy_api 后端）----
    wan_api_key: str = os.getenv("WAN_API_KEY", "")

    # ---- 本地模型目录（可选）----
    # 当 ComfyUI 与网关同机时配置此项，可对模型文件做真实性校验，
    # 避免 object_info 缓存导致"假可用"。多个目录用分号分隔。
    comfy_models_dir: str = os.getenv("COMFY_MODELS_DIR", "")

    # 启动时对本地 checkpoint 做一次真实试跑校准（确定图片类能力是否真实可用）
    comfy_calibrate: bool = os.getenv("COMFY_CALIBRATE", "true").lower() in (
        "1", "true", "yes",
    )


settings = Settings()
