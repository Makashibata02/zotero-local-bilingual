from __future__ import annotations

import json
import time

from fastapi import FastAPI

app = FastAPI()


@app.post("/v1/chat/completions")
async def chat_completions(body: dict) -> dict:
    prompt = str(body.get("messages", [{}])[-1].get("content", ""))
    if "## Here is the input:\n\n" in prompt:
        raw = prompt.split("## Here is the input:\n\n", 1)[-1]
        items = json.loads(raw)
        content = json.dumps(
            [{"id": item["id"], "output": "中译：" + item["input"]} for item in items],
            ensure_ascii=False,
        )
    elif "Now translate the following text:\n\n" in prompt:
        source = prompt.split("Now translate the following text:\n\n", 1)[-1]
        content = "中译：" + source
    else:
        source = prompt.split("Input:\n\n", 1)[-1]
        content = "中译：" + source
    prompt_tokens = max(1, len(prompt) // 4)
    completion_tokens = max(1, len(content) // 3)
    return {
        "id": "mock-completion",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": body.get("model", "mock-model"),
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        },
    }
