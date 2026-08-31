# ComfyForge

**Unified AI Generation Gateway** · 把 ComfyUI 封装成对外 AI 生成服务的统一网关。

单实例承载 **SD 图片（文生图/图生图）+ MiniMax H3 视频（文生视频/图生视频，含原生音频）**；Web 前端与 Agent 通过同一套 REST API 发起生成任务，服务层负责：业务参数 → workflow 模板填充 → ComfyUI 调度 → 产物代理返回。

> 目录名保持 `comfy-service`（本地运行路径），仓库与展示名为 ComfyForge。

| | |
|---|---|
| 后端 | Python 3.10+ / FastAPI / asyncio / httpx |
| 前端 | Vue 3 / Vite |
| 引擎 | ComfyUI（单实例 0.33）+ SD 系列模型 + MiniMax H3-Lite（W4A8 量化视频模型） |
| 交互 | 统一 REST API / 异步任务队列 / 产物代理 |

## 特性

- **多后端自适应**：同一能力自动探测并选择可用后端
  - `comfy_local`：本地/远程 ComfyUI 节点 GPU 推理（SD 图片）
  - `comfy_h3`：MiniMax H3-Lite 本地视频节点 GPU 推理（视频，含原生音频）
  - `comfy_api`：ComfyUI 内置云端 API 节点（如 Wan 云端，需配置密钥）
  - `mock`：无模型/无密钥时自动降级为模拟生成，保证全流程可体验
- **单实例合并部署**：图片与视频共用一个 ComfyUI 实例（`D:\MiniMax-H3\ComfyUI`，官方新版 0.33），通过 `extra_model_paths.yaml` 映射 aki/sd-webui 的 18 个 SD checkpoint + 134 Lora 等资源；网关的 `comfy_local` / `comfy_h3` 两个逻辑后端指向同一实例，按任务类型自动分派
- **多节点可扩展**：`COMFY_SERVERS` 仍支持注册多个物理节点（如远程 GPU 服务器），每个节点独立探测节点集/模型
- **能力探测 + 启动校准**：启动时真实试跑最小任务，确认本地 checkpoint 是否可用，杜绝"假可用"
- **统一任务 API**：`POST /api/v1/tasks` 一个接口创建所有生成任务，异步执行 + 状态轮询 + 产物 URL
- **前后端分离**：FastAPI 网关 + Vue3 前端；前端/Agent/任意系统都走同一套 REST API
- **产物代理**：图片/视频统一从网关按节点代理拉取，前端不直连 ComfyUI

## 架构

```
┌──────────────────────────────────────────────┐
│ 消费者：Vue3 前端 / Agent / 任意 HTTP 客户端    │
└───────────────┬──────────────────────────────┘
                │ 统一业务接口 /api/v1/*
┌───────────────▼──────────────────────────────┐
│ 服务网关 backend/ (FastAPI)                   │
│  · tasks.py    任务 CRUD（JSON / multipart）   │
│  · workflows.py 模板加载·填充·能力探测·校准      │
│  · executor.py  按后端名分派 / Mock 降级        │
│  · comfy.py    ComfyUI HTTP 客户端             │
│  · templates/  4 个 workflow 模板              │
└───────────────┬──────────────────────────────┘
    comfy_local / comfy_h3（两个逻辑后端 → 同一实例）
┌───────────────▼──────────────────────────────┐
│ ComfyUI 单实例（8189, D:\MiniMax-H3\ComfyUI）  │
│  · SD 图片：18 个 checkpoint（extra_model_paths │
│    映射 aki/sd-webui 目录）                    │
│  · H3 视频：W4A8 + qwen3vl + 双 VAE + Turbo    │
└──────────────────────────────────────────────┘
```

## 快速启动

### 0. 启动 ComfyUI 节点

只需启动**一个** ComfyUI 实例（图片与视频共用）：

```bash
# 一键启动（推荐）
scripts\start-h3.bat
# 或手动：
cd D:\MiniMax-H3\ComfyUI
.\venv\Scripts\python.exe main.py --port 8189 --listen 127.0.0.1
```

### 1. 后端（需 Python 3.10+）

```bash
cd backend
pip install -r requirements.txt
# 按需修改 .env（已含本机单实例配置示例）
python run.py
```

后端启动在 `http://127.0.0.1:8000`，交互式 API 文档：`http://127.0.0.1:8000/docs`

### 2. 前端（需 Node 18+）

```bash
cd frontend
npm install
npm run dev
```

前端运行在 `http://localhost:5273`（注意：`5173` 已被占用时改用此端口，见 `vite.config.js`）。

> 也可以双击 `scripts/start-backend.bat` / `scripts/start-frontend.bat` / `scripts/start-h3.bat` 一键启动。

## API 速览

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/v1/health` | 系统状态 + ComfyUI 连接 + 能力探测结果 |
| GET | `/api/v1/capabilities` | 能力列表（含参数定义、激活后端、模型选项） |
| POST | `/api/v1/tasks` | 创建任务（JSON） |
| POST | `/api/v1/tasks/upload` | 创建任务（multipart：图片 + 参数，图生图/图生视频用） |
| GET | `/api/v1/tasks` | 任务列表 |
| GET | `/api/v1/tasks/{id}` | 任务详情（含状态与结果资产） |
| GET | `/api/v1/assets/proxy` | ComfyUI 产物代理 |
| GET | `/api/v1/assets/mock/{file}` | Mock 产物 |

### 创建任务（文生图）

```bash
curl -X POST http://127.0.0.1:8000/api/v1/tasks \
  -H "Content-Type: application/json" \
  -d '{
    "type": "txt2img",
    "params": {
      "prompt": "a serene mountain lake at sunset",
      "model": "AWPainting 1.4\\AWPainting_v1.4.safetensors",
      "width": 512, "height": 512, "steps": 16, "seed": 42
    }
  }'
```

返回 `{id, status, backend}`，随后轮询 `GET /api/v1/tasks/{id}` 直到 `status=succeeded`，从 `result.assets[].url` 取产物。

### Agent 消费示例

```python
# scripts/client_demo.py —— 任何 Agent/脚本都能这样调用
import requests, time

BASE = "http://127.0.0.1:8000/api/v1"

def generate(task_type, params):
    r = requests.post(f"{BASE}/tasks", json={"type": task_type, "params": params})
    task = r.json()
    print("已创建:", task["id"], "| 后端:", task["backend"])
    while True:
        t = requests.get(f"{BASE}/tasks/{task['id']}").json()
        if t["status"] in ("succeeded", "failed"):
            return t
        time.sleep(2)

task = generate("txt2img", {"prompt": "a cute fox in autumn forest", "width": 512, "height": 512})
print("完成:", task["status"])
print("产物:", [a["url"] for a in task["result"]["assets"]])
```

## 配置（backend/.env）

| 变量 | 说明 |
|---|---|
| `COMFY_SERVERS` | ComfyUI 节点，多个用逗号分隔：`comfy_local=http://127.0.0.1:8189,comfy_h3=http://127.0.0.1:8189`（节点名需与模板 backend 名一致；本机合并后两个逻辑后端指向同一实例） |
| `COMFY_MODELS_DIR` | 本地模型根目录（同机部署时填，用于模型文件真实性校验；多个用分号分隔，含 H3 节点目录） |
| `COMFY_CALIBRATE` | 启动时真实试跑校准 checkpoint（默认 true） |
| `COMFY_FORCE_MOCK` | 强制所有能力走模拟模式（演示用） |
| `WAN_API_KEY` | 配置后视频类能力可走 Wan 云端节点 |

## 能力与后端矩阵（当前环境实测）

| 能力 | 激活后端 | 说明 |
|---|---|---|
| 文生图 / 图生图 | `comfy_local`（8189 单实例） | 18 个 checkpoint 真实可用（AWPainting/majicMIX/anything-v5 等，经 extra_model_paths 映射） |
| 文生视频 / 图生视频 | `comfy_h3`（8189 单实例） | MiniMax H3-Lite 真实可用：W4A8 模型 + qwen3vl + 双 VAE + Turbo LoRA，640x352/124帧/4步/24fps，含原生音频（RTX 4060 Ti 实测约 30~110 秒） |

## MiniMax H3 视频节点说明

- 环境位置：`D:\MiniMax-H3\ComfyUI`（独立 venv：`venv\Scripts\python.exe`，复用 aki Python 的 torch 2.9.1+cu130）
- 组件：Set A 低显存路线 —— `minimax_h3_fl2va_pruned_w4a8_mixed`（12.5GB）+ `qwen3vl_4b_int4` 编码器 + `ClipProj` + `video/audio 双 VAE` + `turbo 4step LoRA`
- 合并部署：本实例通过 `extra_model_paths.yaml` 同时映射 aki/sd-webui 的 SD 图片资源（18 个 checkpoint、134 Lora、VAE 等），故图片与视频共用一个 ComfyUI，端口 8189
- 使用 **compat 兼容 workflow**（无 triton 环境不启用 Sage/Sol/BlockCache 加速链，已验证可跑）
- 支持文生视频（T2V）与图生视频（I2V，首帧参考图）；原生带音频输出
- 提示词建议按「场景与氛围 → 动作与镜头 → 声音」三段式写，中文建议 30~50 字以上
- 默认参数 640x352（32 对齐）/ 124 帧 / 4 步 / 24fps；低显存 8GB 路线，显存不足时建议加 `--lowvram` 启动

## 扩展：如何新增一个能力

1. 在 `backend/app/templates/` 新增 `<type>.json`，按现有模板格式写参数与 workflow（支持 `comfy_local` / `comfy_api` 两套后端）。
2. 重启后端，`/api/v1/capabilities` 会自动出现新能力，前端 Tab 自动加载。
3. 视频类能力若依赖特定模型族，在 `requires_models` 里加 `"match": ["关键词"]` 做模型族校验。

## 许可证

MIT License — 自由使用、修改与分发，保留版权声明即可。

## 目录结构

```
comfy-service/
├── backend/                  # FastAPI 网关
│   ├── app/
│   │   ├── main.py           # 应用入口 + 启动校准
│   │   ├── config.py         # 配置
│   │   ├── schemas.py        # Pydantic 模型
│   │   ├── routers/          # system / tasks / assets 路由
│   │   ├── services/         # comfy 客户端 / 模板 / 执行器 / 任务管理
│   │   └── templates/        # 4 个 workflow 模板
│   ├── assets/               # mock 产物
│   ├── run.py                # 启动入口
│   └── .env / .env.example
├── frontend/                 # Vue3 + Vite
│   └── src/
│       ├── App.vue           # 主界面
│       └── components/       # GenerationPanel / TaskList
└── scripts/                  # 一键启动脚本
```
