"""Comfy Service Agent 调用示例。

用法：python client_demo.py [文生图|图生图|文生视频|图生视频]
"""
from __future__ import annotations

import sys
import time

import requests

BASE = "http://127.0.0.1:8000/api/v1"


def generate(task_type: str, params: dict, image_path: str | None = None) -> dict:
    """创建任务并轮询到终态。"""
    if image_path:
        with open(image_path, "rb") as f:
            files = {"image": (image_path.split("\\")[-1], f)}
            data = {"type": task_type, **{k: str(v) for k, v in params.items()}}
            r = requests.post(f"{BASE}/tasks/upload", data=data, files=files)
    else:
        r = requests.post(f"{BASE}/tasks", json={"type": task_type, "params": params})
    r.raise_for_status()
    task = r.json()
    print(f"[{task_type}] 已创建任务 {task['id']}  后端={task['backend']}")

    while True:
        t = requests.get(f"{BASE}/tasks/{task['id']}").json()
        if t["status"] in ("succeeded", "failed"):
            return t
        print(f"  ... {t['status']}")
        time.sleep(2)


def main() -> None:
    demo = sys.argv[1] if len(sys.argv) > 1 else "文生图"

    if demo == "文生图":
        task = generate("txt2img", {
            "prompt": "a cute fox sitting in autumn forest, golden leaves, masterpiece",
            "model": "AWPainting 1.4\\AWPainting_v1.4.safetensors",
            "width": 512, "height": 512, "steps": 14, "seed": 42,
        })
    elif demo == "文生视频":
        task = generate("txt2video", {
            "prompt": "a cat walking in a sunny garden", "width": 480,
            "height": 320, "length": 16, "fps": 10, "seed": 1,
        })
    elif demo == "图生图":
        # 需要一张参考图
        task = generate("img2img", {
            "prompt": "make it a cyberpunk neon style",
            "width": 512, "height": 512, "denoise": 0.6,
        }, image_path=input("参考图路径: "))
    elif demo == "图生视频":
        task = generate("img2video", {
            "prompt": "the cat slowly turns its head", "video_frames": 14,
        }, image_path=input("起始图路径: "))
    else:
        print("未知示例")
        return

    print("状态:", task["status"])
    if task.get("error"):
        print("错误:", task["error"])
    if task.get("result"):
        for a in task["result"]["assets"]:
            print("产物:", a["kind"], "->", a["url"], a.get("note", ""))


if __name__ == "__main__":
    main()
