"""ComfyForge 主流兼容 API 消费示例 / 演示脚本。

覆盖四种能力，全部走兼容接口（/v1/*，OpenAI Images + content[] 视频风格）：

    python scripts/compat_consumer.py txt2img      # 文生图   POST /v1/images/generations
    python scripts/compat_consumer.py img2img      # 图生图   POST /v1/images/edits（multipart）
    python scripts/compat_consumer.py txt2video    # 文生视频 POST /v1/videos/generations（异步轮询）
    python scripts/compat_consumer.py img2video    # 图生视频 POST /v1/videos/generations（content[] 首帧）

不传参数时默认演示 OpenAI SDK 零改动直连 + 文生视频。
"""
import argparse
import base64
import mimetypes
import sys
import time

import requests

BASE = "http://127.0.0.1:8000/v1"
POLL_INTERVAL = 3
TIMEOUT = 600


# ---------------------------------------------------------------------------
# 图片（OpenAI 风格，同步）
# ---------------------------------------------------------------------------
def txt2img(prompt: str = "a serene mountain lake at sunset, misty, cinematic"):
    r = requests.post(f"{BASE}/images/generations", json={
        "prompt": prompt,
        "size": "512x512",
        "quality": "high",
        "response_format": "url",
    }, timeout=180)
    r.raise_for_status()
    data = r.json()["data"][0]
    print("[文生图] 同步返回 ->", data["url"])
    return data["url"]


def img2img(image_path: str, prompt: str = "turn it into a watercolor illustration"):
    with open(image_path, "rb") as f:
        r = requests.post(f"{BASE}/images/edits",
                          data={"prompt": prompt, "size": "512x512"},
                          files={"image": (image_path.split("/")[-1], f,
                                           mimetypes.guess_type(image_path)[0] or "image/png")},
                          timeout=180)
    r.raise_for_status()
    url = r.json()["data"][0]["url"]
    print("[图生图] 同步返回 ->", url)
    return url


# ---------------------------------------------------------------------------
# 视频（主流异步三步式：创建 -> 轮询 -> 产物 URL）
# ---------------------------------------------------------------------------
def _local_image_to_data_uri(path: str) -> str:
    """把本地图片转 data: URI，避免视频接口单独做文件上传。"""
    mime = mimetypes.guess_type(path)[0] or "image/png"
    with open(path, "rb") as f:
        return f"data:{mime};base64,{base64.b64encode(f.read()).decode()}"


def txt2video(prompt: str = "a white dog running in a cherry blossom park, petals swirling, birds chirping"):
    return _create_and_poll("txt2video", [{"type": "text", "text": prompt}])


def img2video(image_path: str, prompt: str = "the subject slowly turns its head and blinks, gentle camera zoom"):
    frame = _local_image_to_data_uri(image_path)
    return _create_and_poll("img2video", [
        {"type": "text", "text": prompt},
        {"type": "image_url", "image_url": {"url": frame}, "role": "first_frame"},
    ])


def _create_and_poll(kind: str, content: list) -> dict:
    r = requests.post(f"{BASE}/videos/generations", json={
        "model": "MiniMax-H3",
        "content": content,
        "duration": 5,
        "ratio": "16:9",
        "resolution": "480P",
        "seed": -1,
    }, timeout=30)
    r.raise_for_status()
    task_id = r.json()["task_id"]
    print(f"[{kind}] 任务已创建: {task_id}，轮询中...")
    deadline = time.time() + TIMEOUT
    while time.time() < deadline:
        s = requests.get(f"{BASE}/videos/generations/{task_id}", timeout=10).json()
        if s["status"] in ("succeeded", "failed"):
            print(f"[{kind}] {s['status']} ->", s.get("content", s.get("error")))
            return s
        time.sleep(POLL_INTERVAL)
    print(f"[{kind}] 轮询超时")
    return {"status": "timeout"}


# ---------------------------------------------------------------------------
# OpenAI SDK 零改动直连演示
# ---------------------------------------------------------------------------
def openai_sdk_demo():
    from openai import OpenAI

    client = OpenAI(base_url=BASE, api_key="any")
    resp = client.images.generate(
        model="gpt-image-1",
        prompt="a cyberpunk neon city street at night, rain, reflections",
        size="512x512",
        n=1,
    )
    print("OpenAI SDK 直连 ->", resp.data[0].url)


def main():
    ap = argparse.ArgumentParser(description="ComfyForge 兼容 API 四种能力演示")
    ap.add_argument("kind", nargs="?", default="sdk",
                    choices=["txt2img", "img2img", "txt2video", "img2video", "sdk"])
    ap.add_argument("image", nargs="?", default=None, help="img2img/img2video 的参考图路径")
    args = ap.parse_args()

    if args.kind == "txt2img":
        txt2img()
    elif args.kind == "img2img":
        if not args.image:
            print("图生图需要参考图路径: python scripts/compat_consumer.py img2img D:\\photo.png"); sys.exit(1)
        img2img(args.image)
    elif args.kind == "txt2video":
        txt2video()
    elif args.kind == "img2video":
        if not args.image:
            print("图生视频需要起始图路径: python scripts/compat_consumer.py img2video D:\\frame.png"); sys.exit(1)
        img2video(args.image)
    else:
        openai_sdk_demo()
        txt2video()


if __name__ == "__main__":
    main()
