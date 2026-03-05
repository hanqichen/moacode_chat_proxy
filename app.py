import argparse
import hashlib
import json
import os
import time
import uuid
from pathlib import Path
from typing import Iterator
from typing import Any, Dict, List, Optional

import httpx
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

MOACODE_BASE_URL = "https://moacode.org/v1"
DEFAULT_AUTH_JSON_PATH = Path.home() / ".codex" / "auth.json"
AUTH_JSON_PATH_OVERRIDE: Optional[Path] = None

app = FastAPI(title="Moacode Chat->Responses Proxy", version="0.1.0")
SUPPORTED_TOP_LEVEL_CACHE_FIELDS = {
    "prompt_cache_key",
    "enable_caching",
}
DEFAULT_AUTO_PROMPT_CACHE_PREFIX_CHARS = 4096
DEFAULT_AUTO_PROMPT_CACHE_MIN_MESSAGES = 6
DEFAULT_AUTO_PROMPT_CACHE_UA_SEGMENTS = 6
DEFAULT_UPSTREAM_TRUST_ENV = False


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


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _is_enabled_env(var_name: str, default: bool = False) -> bool:
    raw = os.getenv(var_name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"0", "false", "off", "no", ""}


def _upstream_trust_env() -> bool:
    return _is_enabled_env("UPSTREAM_TRUST_ENV", default=DEFAULT_UPSTREAM_TRUST_ENV)


def map_usage(input_text: str, output_text: str, upstream_usage: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if isinstance(upstream_usage, dict):
        prompt_tokens = _safe_int(upstream_usage.get("input_tokens") or upstream_usage.get("prompt_tokens") or 0)
        completion_tokens = _safe_int(upstream_usage.get("output_tokens") or upstream_usage.get("completion_tokens") or 0)
        total_tokens = _safe_int(upstream_usage.get("total_tokens"), prompt_tokens + completion_tokens)

        usage: Dict[str, Any] = {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
        }
        prompt_details = upstream_usage.get("prompt_tokens_details")
        if not isinstance(prompt_details, dict):
            prompt_details = upstream_usage.get("input_tokens_details")
        if isinstance(prompt_details, dict):
            usage["prompt_tokens_details"] = prompt_details

        completion_details = upstream_usage.get("completion_tokens_details")
        if not isinstance(completion_details, dict):
            completion_details = upstream_usage.get("output_tokens_details")
        if isinstance(completion_details, dict):
            usage["completion_tokens_details"] = completion_details

        cached_tokens = upstream_usage.get("cached_tokens")
        if cached_tokens is not None:
            details = usage.get("prompt_tokens_details", {})
            if isinstance(details, dict):
                details = dict(details)
                details.setdefault("cached_tokens", _safe_int(cached_tokens))
                usage["prompt_tokens_details"] = details

        return usage

    prompt_tokens = max(1, len(input_text) // 4) if input_text else 0
    completion_tokens = max(1, len(output_text) // 4) if output_text else 0
    return {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": prompt_tokens + completion_tokens,
    }


def pass_through_cache_params(chat_body: Dict[str, Any], upstream_payload: Dict[str, Any]) -> None:
    # Keep this intentionally narrow: only forward known top-level cache knobs.
    for key in SUPPORTED_TOP_LEVEL_CACHE_FIELDS:
        if key in chat_body:
            upstream_payload[key] = chat_body[key]


def _normalize_cache_text(text: str) -> str:
    return " ".join(text.split())


def _canonicalize_tool_arguments(arguments: Any) -> str:
    if isinstance(arguments, str):
        arguments_text = arguments.strip()
        if not arguments_text:
            return ""
        try:
            parsed = json.loads(arguments_text)
            return json.dumps(parsed, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        except Exception:
            return _normalize_cache_text(arguments_text)
    if isinstance(arguments, (dict, list)):
        return json.dumps(arguments, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    if arguments is None:
        return ""
    return _normalize_cache_text(str(arguments))


def _build_system_prefix_for_cache(messages: List[Dict[str, Any]], prefix_chars: int) -> str:
    system_parts: List[str] = []
    for msg in messages:
        if msg.get("role") != "system":
            continue
        content = _normalize_cache_text(_content_to_text(msg.get("content", "")))
        if content:
            system_parts.append(content)
    return "\n".join(system_parts)[:prefix_chars]


def _build_ua_segments_for_cache(messages: List[Dict[str, Any]], max_segments: int) -> List[str]:
    segments: List[str] = []
    for msg in messages:
        role = str(msg.get("role", ""))
        if role not in {"user", "assistant"}:
            continue

        parts: List[str] = []
        content = _normalize_cache_text(_content_to_text(msg.get("content", "")))
        if content:
            parts.append(content)

        if role == "assistant":
            tool_calls = msg.get("tool_calls")
            if isinstance(tool_calls, list) and tool_calls:
                call_parts: List[str] = []
                for call in tool_calls:
                    if not isinstance(call, dict):
                        continue
                    fn = call.get("function")
                    if not isinstance(fn, dict):
                        continue
                    name = _normalize_cache_text(str(fn.get("name", "")))
                    args = _canonicalize_tool_arguments(fn.get("arguments"))
                    if name and args:
                        call_parts.append(f"{name}({args})")
                    elif name:
                        call_parts.append(name)
                if call_parts:
                    parts.append("tool_calls:" + "|".join(call_parts))

        if not parts:
            continue

        segments.append(f"{role}:{' '.join(parts)}")
        if len(segments) >= max_segments:
            break
    return segments


def maybe_inject_prompt_cache_key(upstream_payload: Dict[str, Any], messages: List[Dict[str, Any]]) -> None:
    # Keep behavior simple: caller key always wins; auto mode only fills missing key.
    if upstream_payload.get("prompt_cache_key"):
        return
    if not _is_enabled_env("AUTO_PROMPT_CACHE_KEY", default=True):
        return

    prefix_chars = _safe_int(
        os.getenv("AUTO_PROMPT_CACHE_KEY_PREFIX_CHARS"),
        DEFAULT_AUTO_PROMPT_CACHE_PREFIX_CHARS,
    )
    if prefix_chars <= 0:
        prefix_chars = DEFAULT_AUTO_PROMPT_CACHE_PREFIX_CHARS

    min_messages = _safe_int(
        os.getenv("AUTO_PROMPT_CACHE_MIN_MESSAGES"),
        DEFAULT_AUTO_PROMPT_CACHE_MIN_MESSAGES,
    )
    if min_messages <= 0:
        min_messages = DEFAULT_AUTO_PROMPT_CACHE_MIN_MESSAGES

    ua_message_count = sum(1 for msg in messages if str(msg.get("role", "")) in {"user", "assistant"})
    if ua_message_count < min_messages:
        return

    system_prefix = _build_system_prefix_for_cache(messages, prefix_chars)
    ua_segments = _build_ua_segments_for_cache(messages, DEFAULT_AUTO_PROMPT_CACHE_UA_SEGMENTS)
    if not system_prefix and not ua_segments:
        return

    key_parts = [f"model={upstream_payload.get('model', '')}", f"system={system_prefix}"]
    for idx, segment in enumerate(ua_segments, start=1):
        key_parts.append(f"ua{idx}={segment}")
    key_material = "\n".join(key_parts)
    digest = hashlib.sha256(key_material.encode("utf-8")).hexdigest()
    upstream_payload["prompt_cache_key"] = f"auto-pck-v2-{digest[:48]}"


def stream_chat_completion_response(
    completion_id: str,
    created: int,
    model: str,
    assistant_text: str,
    usage: Dict[str, Any],
    include_usage: bool,
) -> Iterator[bytes]:
    def _event(payload: Dict[str, Any]) -> bytes:
        return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n".encode("utf-8")

    # Start chunk announces role, then content, then finish reason.
    yield _event(
        {
            "id": completion_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": model,
            "choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}],
        }
    )

    if assistant_text:
        yield _event(
            {
                "id": completion_id,
                "object": "chat.completion.chunk",
                "created": created,
                "model": model,
                "choices": [{"index": 0, "delta": {"content": assistant_text}, "finish_reason": None}],
            }
        )

    yield _event(
        {
            "id": completion_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": model,
            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
        }
    )

    if include_usage:
        yield _event(
            {
                "id": completion_id,
                "object": "chat.completion.chunk",
                "created": created,
                "model": model,
                "choices": [],
                "usage": usage,
            }
        )

    yield b"data: [DONE]\n\n"


@app.get("/health")
def health() -> Dict[str, str]:
    return {"status": "ok"}


@app.post("/v1/chat/completions")
async def chat_completions(request: Request, authorization: Optional[str] = Header(default=None)) -> Any:
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
    stream = bool(body.get("stream"))
    stream_options = body.get("stream_options")
    include_usage = isinstance(stream_options, dict) and bool(stream_options.get("include_usage"))
    if not model or not isinstance(messages, list):
        raise HTTPException(status_code=400, detail="model and messages are required")
    if any(not isinstance(msg, dict) for msg in messages):
        raise HTTPException(status_code=400, detail="each message must be an object")

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
    if "max_completion_tokens" in body:
        upstream_payload["max_output_tokens"] = body["max_completion_tokens"]
    pass_through_cache_params(body, upstream_payload)
    maybe_inject_prompt_cache_key(upstream_payload, messages)

    upstream_token = get_upstream_token()
    if not upstream_token:
        raise HTTPException(status_code=500, detail="No upstream API key found")

    headers = {
        "Authorization": f"Bearer {upstream_token}",
        "Content-Type": "application/json",
    }

    try:
        timeout = httpx.Timeout(connect=30.0, read=30.0, write=30.0, pool=30.0)
        async with httpx.AsyncClient(timeout=timeout, trust_env=_upstream_trust_env()) as client:
            resp = await client.post(f"{MOACODE_BASE_URL}/responses", json=upstream_payload, headers=headers)
    except Exception as exc:
        error_detail = {
            "error": {
                "message": "Upstream request failed",
                "upstream_status": None,
                "upstream_body": f"{exc.__class__.__name__}: {str(exc) or repr(exc)}"[:2048],
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

    if stream:
        return StreamingResponse(
            stream_chat_completion_response(
                completion_id=completion_id,
                created=created,
                model=resp_json.get("model") or model,
                assistant_text=assistant_text,
                usage=usage,
                include_usage=include_usage,
            ),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
        )

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
    print(f"[startup] upstream_trust_env={_upstream_trust_env()}")
    uvicorn.run(app, host="0.0.0.0", port=args.port)
