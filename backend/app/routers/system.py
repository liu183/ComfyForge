"""系统接口：健康检查、能力探测。"""
from __future__ import annotations

from fastapi import APIRouter, Request

from ..schemas import CapabilityOut, HealthOut, ServerOut
from ..services.workflows import build_capability

router = APIRouter(tags=["system"])


@router.get("/health", response_model=HealthOut)
async def health(request: Request):
    st = request.app.state
    servers = []
    for name, srv in st.comfy_servers.items():
        servers.append(ServerOut(
            name=name,
            url=srv.base_url,
            reachable=srv.reachable,
            version=srv.version,
            error=srv.error,
        ))
    capabilities: list[CapabilityOut] = []
    for tpl in st.templates:
        capabilities.append(
            build_capability(
                tpl,
                st.primary_server,
                st.object_info or {},
                st.comfy_servers,
                st.object_infos or {},
            )
        )
    return HealthOut(
        app=st.settings.app_name,
        version=st.app_version,
        comfy_servers=servers,
        capabilities=capabilities,
        force_mock=st.settings.force_mock,
    )


@router.get("/capabilities", response_model=list[CapabilityOut])
async def capabilities(request: Request):
    st = request.app.state
    out = []
    for tpl in st.templates:
        out.append(
            build_capability(
                tpl,
                st.primary_server,
                st.object_info or {},
                st.comfy_servers,
                st.object_infos or {},
            )
        )
    return out
