"""Temporary demo for streaming xlab responses with httpx."""

import json
import time
from pathlib import Path

import httpx


def load_xlab_config() -> tuple[str, str]:
    config_path = Path.home() / ".config" / "opencode" / "opencode.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    options = config["provider"]["xlab"]["options"]
    return options["baseURL"].rstrip("/"), options["apiKey"]


def main() -> None:
    base_url, api_key = load_xlab_config()
    prompt = "请用三句话解释 httpx stream 是什么，语气适合 Python 初学者。"

    for attempt in range(3):
        try:
            stream_response(base_url, api_key, prompt)
            return
        except httpx.HTTPError as exc:
            if attempt == 2:
                raise
            print(f"连接失败，重试中: {exc}")
            time.sleep(1)


def stream_response(base_url: str, api_key: str, prompt: str) -> None:

    with httpx.stream(
        "POST",
        f"{base_url}/chat/completions",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json={
            "model": "gpt-5.5",
            "messages": [{"role": "user", "content": prompt}],
            "stream": True,
            "max_completion_tokens": 300,
        },
        timeout=60,
    ) as response:
        print("status:", response.status_code)
        response.raise_for_status()
        print("stream:\n")

        for line in response.iter_lines():
            if not line or not line.startswith("data: "):
                continue

            data = line.removeprefix("data: ")
            if data == "[DONE]":
                break

            chunk = json.loads(data)
            delta = chunk["choices"][0].get("delta", {}).get("content") or ""
            print(delta, end="", flush=True)

        print()


if __name__ == "__main__":
    main()
