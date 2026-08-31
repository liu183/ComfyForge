"""ComfyForge 主流兼容 API 消费示例。

演示两种消费方式：
  1) OpenAI SDK 零改动直连图片生成（/v1/images/generations）
  2) content[] 多模态视频生成（/v1/videos/generations），适配 MiniMax H3 / Seedance 风格
"""
import time

BASE = "http://127.0.0.1:8000/v1"


def openai_sdk_demo():
    """方式一：OpenAI SDK 直连（改 base_url 即可）。"""
    from openai import OpenAI

    client = OpenAI(base_url=BASE, api_key="any")
    resp = client.images.generate(
        model="gpt-image-1",
        prompt="a cyberpunk neon city street at night, rain, reflections",
        size="512x512",
        n=1,
    )
    print("OpenAI SDK 直连 ->", resp.data[0].url)


def video_content_demo(first_frame_url: str | None = None):
    """方式二：content[] 视频生成（文生视频 / 图生视频自动识别）。"""
    import requests

    content = [{"type": "text", "text": "竹林晨雾，风吹竹叶轻轻摇曳，雾气流动"}]
    if first_frame_url:
        content.append({
            "type": "image_url",
            "image_url": {"url": first_frame_url},
            "role": "first_frame",
        })

    r = requests.post(f"{BASE}/videos/generations", json={
        "model": "MiniMax-H3",
        "content": content,
        "duration": 5,      # 秒
        "ratio": "16:9",
        "resolution": "480P",
        "seed": -1,         # 负数为随机种子
    }, timeout=30)
    r.raise_for_status()
    task_id = r.json()["task_id"]
    print("视频任务已创建:", task_id)

    while True:
        s = requests.get(f"{BASE}/videos/generations/{task_id}", timeout=10).json()
        if s["status"] in ("succeeded", "failed"):
            print("状态:", s["status"], "->", s.get("content", s.get("error")))
            return
        time.sleep(3)


if __name__ == "__main__":
    openai_sdk_demo()
    video_content_demo()
