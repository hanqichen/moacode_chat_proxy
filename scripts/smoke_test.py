#!/usr/bin/env python3
import argparse
import json
import sys
import urllib.error
import urllib.request
from typing import Dict, Tuple


def http_get(url: str, timeout: int) -> Tuple[int, str]:
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        body = resp.read().decode("utf-8", errors="replace")
        return resp.status, body


def http_post_json(url: str, payload: Dict, timeout: int, api_key: str) -> Tuple[int, str]:
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url=url, data=data, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read().decode("utf-8", errors="replace")
        return resp.status, body


def main() -> int:
    parser = argparse.ArgumentParser(description="moacode_chat_proxy 快速自测脚本")
    parser.add_argument("--host", default="127.0.0.1", help="代理地址")
    parser.add_argument("--port", type=int, default=5102, help="代理端口")
    parser.add_argument("--model", default="gpt-5.3-codex", help="测试模型")
    parser.add_argument("--message", default="ping", help="测试消息")
    parser.add_argument("--api-key", default="", help="如果启用 INBOUND_BEARER，请填该值")
    parser.add_argument("--timeout", type=int, default=60, help="请求超时秒数")
    args = parser.parse_args()

    base_url = f"http://{args.host}:{args.port}"

    print(f"[1/2] 健康检查: GET {base_url}/health")
    try:
        status, body = http_get(f"{base_url}/health", args.timeout)
        print(f"health 状态码={status} 响应={body}")
    except Exception as exc:
        print(f"[FAIL] 健康检查失败: {exc}")
        return 1

    print(f"[2/2] 对话接口检查: POST {base_url}/v1/chat/completions")
    payload = {
        "model": args.model,
        "messages": [{"role": "user", "content": args.message}],
    }
    try:
        status, body = http_post_json(
            f"{base_url}/v1/chat/completions",
            payload,
            timeout=args.timeout,
            api_key=args.api_key,
        )
        print(f"chat 状态码={status}")
        print(body)
    except urllib.error.HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="replace")
        print(f"[FAIL] HTTP {exc.code}")
        print(error_body)
        return 2
    except Exception as exc:
        print(f"[FAIL] 请求失败: {exc}")
        return 2

    if status != 200:
        print("[FAIL] 代理可访问，但上游调用未成功")
        return 3

    print("\n[OK] 代理测试通过")
    print("客户端配置（OpenAI 兼容客户端）:")
    print(f"  base_url = {base_url}/v1")
    if args.api_key:
        print(f"  api_key  = {args.api_key}")
    else:
        print("  api_key  = 任意非空字符串（未设置 INBOUND_BEARER 时）")
        print("             若设置 INBOUND_BEARER，则必须使用该值")

    print("\n本项目代理上游配置:")
    print("  upstream_base_url = https://moacode.org/v1")
    print("  upstream_api_key 读取优先级:")
    print("    1) MOACODE_API_KEY")
    print("    2) auth.json.APIROUTER_API_KEY")
    print("    3) auth.json.OPENAI_API_KEY")
    return 0


if __name__ == "__main__":
    sys.exit(main())
