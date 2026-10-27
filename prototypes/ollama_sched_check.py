"""Spike S-3: Ollama residency and cancellation behaviour on the 6GB box.

1. Does a CPU-forced embedding call (num_gpu=0) evict the resident LLM?
2. Does a request with a different num_ctx force a reload?
3. Does closing a streaming request stop generation fast enough for a real-time request to start promptly?
"""
import threading
import time

import httpx

BASE = "http://127.0.0.1:11434"
LLM = "qwen3.5:4b"
EMB = "qwen3-embedding:0.6b"


def ps():
    return {m["name"]: m.get("size_vram", 0) // 2**20 for m in httpx.get(f"{BASE}/api/ps").json()["models"]}


def chat(prompt: str, num_ctx: int = 4096, max_tokens: int = 32):
    t0 = time.perf_counter()
    r = httpx.post(f"{BASE}/api/chat", json={
        "model": LLM, "stream": False, "think": False, "keep_alive": "30m",
        "messages": [{"role": "user", "content": prompt}],
        "options": {"num_ctx": num_ctx, "num_predict": max_tokens, "temperature": 0},
    }, timeout=300).json()
    return time.perf_counter() - t0, r.get("load_duration", 0) / 1e9


print("start ps:", ps())
el, load = chat("안녕하세요라고만 답해.")
print(f"warm chat num_ctx=4096: {el:.2f}s load={load:.2f}s ps={ps()}")

t0 = time.perf_counter()
e = httpx.post(f"{BASE}/api/embed", json={"model": EMB, "input": ["팀 프로젝트 갈등 해결 경험", "협업 중 의견 충돌"],
                                          "keep_alive": "30m", "options": {"num_gpu": 0}}, timeout=300).json()
print(f"embed (num_gpu=0): {time.perf_counter() - t0:.2f}s dim={len(e['embeddings'][0])} ps={ps()}")
el, load = chat("안녕하세요라고만 답해.")
print(f"chat after embed: {el:.2f}s load={load:.2f}s ps={ps()}")

el, load = chat("안녕하세요라고만 답해.", num_ctx=8192)
print(f"chat with num_ctx=8192: {el:.2f}s load={load:.2f}s ps={ps()}")
el, load = chat("안녕하세요라고만 답해.", num_ctx=4096)
print(f"back to num_ctx=4096: {el:.2f}s load={load:.2f}s")

# Cancellation: long streaming generation, abort after 1s, then time a short real-time request.
stop = threading.Event()


def long_stream():
    with httpx.stream("POST", f"{BASE}/api/chat", json={
        "model": LLM, "stream": True, "think": False,
        "messages": [{"role": "user", "content": "한국 역사를 아주 길게 3000자 이상 설명해."}],
        "options": {"num_ctx": 4096, "num_predict": 2000},
    }, timeout=300) as resp:
        for _ in resp.iter_lines():
            if stop.is_set():
                break  # leaving the context manager closes the connection


th = threading.Thread(target=long_stream)
th.start()
time.sleep(1.5)
stop.set()
th.join()
el, _ = chat("숫자 1만 답해.", max_tokens=8)
print(f"short request right after aborting a long stream: {el:.2f}s")

# Without cancellation: queue behind a long generation.
th = threading.Thread(target=lambda: chat("한국 역사를 아주 길게 설명해.", max_tokens=600))
th.start()
time.sleep(0.5)
el, _ = chat("숫자 1만 답해.", max_tokens=8)
th.join()
print(f"short request queued behind a 600-token generation (no cancel): {el:.2f}s")
