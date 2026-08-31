"""Comfy Service —— 把 ComfyUI 封装成对外 AI 生成服务的网关。"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import settings
from .routers import assets, system, tasks
from .services.comfy import ComfyServer
from .services.task_manager import TaskManager
from .services.workflows import load_templates

APP_VERSION = "0.1.0"


class AppState:
    def __init__(self):
        self.settings = settings
        self.app_version = APP_VERSION
        self.assets_dir = settings.assets_dir
        self.templates = load_templates()
        self.comfy_servers: dict[str, ComfyServer] = {}
        self.primary_server: ComfyServer | None = None
        self.object_info: dict | None = None
        self.task_manager: TaskManager | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    st: AppState = app.state
    settings.assets_dir.mkdir(parents=True, exist_ok=True)

    # 初始化 ComfyUI 节点连接
    for name, url in settings.comfy_servers.items():
        st.comfy_servers[name] = ComfyServer(name, url)

    # 并行探测所有节点
    async def _check(srv: ComfyServer):
        await srv.check()

    await asyncio.gather(*[s.check() for s in st.comfy_servers.values()])

    # 选第一个可达节点为主节点，用于图片类能力默认视图
    st.primary_server = next(
        (s for s in st.comfy_servers.values() if s.reachable), None
    )
    # 为每个可达节点拉取独立 object_info（不同节点承载不同节点集/模型）
    st.object_infos: dict[str, dict] = {}
    if st.primary_server:
        for name, srv in st.comfy_servers.items():
            if not srv.reachable:
                continue
            try:
                st.object_infos[name] = await srv.object_info()
            except Exception as e:  # noqa: BLE001
                print(f"[main] 获取 {name} object_info 失败: {e}")
                st.object_infos[name] = {}
        st.object_info = st.object_infos.get(st.primary_server.name, {})

    # 初始化任务管理器 + 执行器
    st.task_manager = TaskManager(st.assets_dir, {
        t["type"]: t for t in st.templates
    })
    if st.primary_server:
        from .services.executor import ComfyExecutor

        st.task_manager.comfy_executor = ComfyExecutor(
            st.comfy_servers, st.primary_server
        )

    # 同机模式：真实试跑校准本地 checkpoint（决定图片类能力是否真实可用）
    if (
        settings.comfy_models_dir
        and settings.comfy_calibrate
        and st.primary_server
        and st.object_info
    ):
        from .services.workflows import (
            calibrate_image_backend,
            set_calibration,
        )

        print("[main] 正在校准本地 checkpoint（最小试跑）...")
        ok, reason = await calibrate_image_backend(
            st.primary_server, st.object_info
        )
        set_calibration(ok, reason)
        print(f"[main] 校准结果: {reason}")

    print(
        f"[comfy-service] 节点 {len(st.comfy_servers)} 个，"
        f"可达 {sum(1 for s in st.comfy_servers.values() if s.reachable)} 个，"
        f"模板 {len(st.templates)} 个"
    )
    yield


def create_app() -> FastAPI:
    app = FastAPI(
        title="Comfy Service",
        description="把 ComfyUI 封装为对外 AI 生成服务：文生图 / 图生图 / 文生视频 / 图生视频。"
        "Web 前端与 Agent 均可通过统一 REST API 消费。",
        version=APP_VERSION,
        lifespan=lifespan,
    )
    app.state = AppState()

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    prefix = settings.api_prefix
    app.include_router(system.router, prefix=prefix)
    app.include_router(tasks.router, prefix=prefix)
    app.include_router(assets.router, prefix=prefix)

    # 主流兼容 API（OpenAI Images / 主流视频生成 content[] 风格），挂载在 /v1
    from .routers import compat

    app.include_router(compat.router, prefix="/v1")

    @app.get("/")
    async def root():
        return {
            "app": settings.app_name,
            "docs": "/docs",
            "api": f"{prefix}/health",
        }

    return app


app = create_app()
