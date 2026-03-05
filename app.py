import argparse
import json
import os
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse

MOACODE_BASE_URL = "https://moacode.org/v1"
DEFAULT_AUTH_JSON_PATH = Path.home() / ".codex" / "auth.json"
AUTH_JSON_PATH_OVERRIDE: Optional[Path] = None

app = FastAPI(title="Moacode Chat->Responses Proxy", version="0.1.0")


def _mask_token(token: str) -> str:
    if not token:
        return "<empty>"
    if len(token) <= 10:
        return token[:2] + "***"
    return f"{token[:6]}***{token[-4:]}"


def get_auth_json_path() -> Path:
    if AUTH_JSON_PATH_OVERRIDE is not None:
        return AUTH_JSON_PATH_OVERRIDE
    env_path = os.getenv("CODEX_AUTH_JSON_PATH")
    if env_path:
        return Path(env_path).expanduser()
    return DEFAULT_AUTH_JSON_PATH


def _read_auth_json() -> Dict[str, Any]:
    auth_json_path = get_auth_json_path()
    if not auth_json_path.exists():
        return {}
    try:
        with auth_json_path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def get_upstream_token() -> str:
    env_token = os.getenv("MOACODE_API_KEY")
    if env_token:
        return env_token

    auth_data = _read_auth_json()
    if auth_data.get("APIROUTER_API_KEY"):
        return auth_data["APIROUTER_API_KEY"]
    if auth_data.get("OPENAI_API_KEY"):
        return auth_data["OPENAI_API_KEY"]
    return ""


def _content_to_text(content: Any) -> str:
    if isinstance(content, str):
        return content

    if isinstance(content, list):
        parts: List[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                if isinstance(item.get("text"), str):
                    parts.append(item["text"])
                elif item.get("type") == "text" and isinstance(item.get("content"), str):
                    parts.append(item["content"])
                else:
                    parts.append(json.dumps(item, ensure_ascii=False))
            else:
                parts.append(str(item))
        return "\n".join(p for p in parts if p)

    if isinstance(content, dict):
        if isinstance(content.get("text"), str):
            return content["text"]
        return json.dumps(content, ensure_ascii=False)

    return str(content)


def messages_to_input(messages: List[Dict[str, Any]]) -> str:
    systems: List[Dict[str, Any]] = []
    others: List[Dict[str, Any]] = []

    for msg in messages:
        if msg.get("role") == "system":
            systems.append(msg)
        else:
            others.append(msg)

    ordered = systems + others
    lines: List[str] = []
    for msg in ordered:
        role = str(msg.get("role", "user"))
        content = _content_to_text(msg.get("content", ""))
        lines.append(f"[{role}] {content}".strip())

    return "\n".join(lines)


def extract_output_text(resp_json: Dict[str, Any]) -> str:
    output = resp_json.get("output")
    if not isinstance(output, list):
        return json.dumps(resp_json, ensure_ascii=False)

    result_parts: List[str] = []
    for block in output:
        if not isinstance(block, dict):
            result_parts.append(str(block))
            continue

        content_list = block.get("content")
        if not isinstance(content_list, list):
            result_parts.append(json.dumps(block, ensure_ascii=False))
            continue

        for content in content_list:
            if isinstance(content, dict) and isinstance(content.get("text"), str):
                result_parts.append(content["text"])
            else:
                result_parts.append(json.dumps(content, ensure_ascii=False))

    joined = "".join(result_parts).strip()
    if joined:
        return joined
    return json.dumps(resp_json, ensure_ascii=False)


def map_usage(input_text: str, output_text: str, upstream_usage: Optional[Dict[str, Any]]) -> Dict[str, int]:
    if isinstance(upstream_usage, dict):
        prompt_tokens = int(upstream_usage.get("input_tokens") or upstream_usage.get("prompt_tokens") or 0)
        completion_tokens = int(upstream_usage.get("output_tokens") or upstream_usage.get("completion_tokens") or 0)
        total_tokens = int(upstream_usage.get("total_tokens") or (prompt_tokens + completion_tokens))
        return {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
        }

    prompt_tokens = max(1, len(input_text) // 4) if input_text else 0
    completion_tokens = max(1, len(output_text) // 4) if output_text else 0
    return {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": prompt_tokens + completion_tokens,
    }


@app.get("/health")
def health() -> Dict[str, str]:
    return {"status": "ok"}


@app.post("/v1/chat/completions")
async def chat_completions(request: Request, authorization: Optional[str] = Header(default=None)) -> JSONResponse:
    inbound_bearer = os.getenv("INBOUND_BEARER")
    if inbound_bearer:
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="Missing Authorization Bearer token")
        token = authorization[7:]
        if token != inbound_bearer:
            raise HTTPException(status_code=401, detail="Invalid inbound token")

    body = await request.json()

    model = body.get("model")
    messages = body.get("messages")
    if not model or not isinstance(messages, list):
        raise HTTPException(status_code=400, detail="model and messages are required")

    input_text = messages_to_input(messages)

    upstream_payload: Dict[str, Any] = {
        "model": model,
        "input": input_text,
    }

    if "temperature" in body:
        upstream_payload["temperature"] = body["temperature"]
    if "top_p" in body:
        upstream_payload["top_p"] = body["top_p"]
    if "max_tokens" in body:
        upstream_payload["max_output_tokens"] = body["max_tokens"]

    upstream_token = get_upstream_token()
    if not upstream_token:
        raise HTTPException(status_code=500, detail="No upstream API key found")

    headers = {
        "Authorization": f"Bearer {upstream_token}",
        "Content-Type": "application/json",
    }

    try:
        timeout = httpx.Timeout(connect=30.0, read=30.0, write=30.0, pool=30.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(f"{MOACODE_BASE_URL}/responses", json=upstream_payload, headers=headers)
    except Exception as exc:
        error_detail = {
            "error": {
                "message": "Upstream request failed",
                "upstream_status": None,
                "upstream_body": str(exc)[:2048],
            }
        }
        return JSONResponse(status_code=502, content=error_detail)

    if resp.status_code >= 400:
        error_detail = {
            "error": {
                "message": "Upstream returned non-2xx",
                "upstream_status": resp.status_code,
                "upstream_body": resp.text[:2048],
            }
        }
        return JSONResponse(status_code=502, content=error_detail)

    resp_json = resp.json()
    assistant_text = extract_output_text(resp_json)

    created = int(time.time())
    completion_id = f"chatcmpl-{uuid.uuid4().hex}"
    usage = map_usage(input_text, assistant_text, resp_json.get("usage"))

    chat_resp = {
        "id": completion_id,
        "object": "chat.completion",
        "created": created,
        "model": resp_json.get("model") or model,
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": assistant_text,
                },
                "finish_reason": "stop",
            }
        ],
        "usage": usage,
    }

    return JSONResponse(status_code=200, content=chat_resp)


if __name__ == "__main__":
    import uvicorn

    parser = argparse.ArgumentParser(description="Moacode Chat Completions -> Responses proxy")
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.getenv("PORT", "5102")),
        help="Listening port (default: env PORT or 5102)",
    )
    parser.add_argument(
        "--auth-json-path",
        default=os.getenv("CODEX_AUTH_JSON_PATH"),
        help="Path to codex auth.json (default: env CODEX_AUTH_JSON_PATH or ~/.codex/auth.json)",
    )
    args = parser.parse_args()

    if args.auth_json_path:
        AUTH_JSON_PATH_OVERRIDE = Path(args.auth_json_path).expanduser()

    token = get_upstream_token()
    print(f"[startup] auth_json_path={get_auth_json_path()}")
    print(f"[startup] token_loaded={bool(token)} token={_mask_token(token)}")
    uvicorn.run(app, host="0.0.0.0", port=args.port)
