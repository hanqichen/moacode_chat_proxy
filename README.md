# moacode_chat_proxy

把 `POST /v1/chat/completions` 转换为上游 moacode 的 `POST /v1/responses`，用于兼容只支持 Chat Completions 的客户端（如 OpenClaw）。

## 1) 主要目的（对外）

- 让只会调用 OpenAI Chat Completions 的客户端，直接接入 moacode。
- 对客户端暴露 OpenAI 兼容入口：`POST /v1/chat/completions`。
- 由代理在服务端完成协议转换与上游鉴权处理。

## 2) 功能

- 对外接口：`POST /v1/chat/completions`
- 健康检查：`GET /health` -> `{"status":"ok"}`
- 自动读取上游 key（优先级）：
  1. `MOACODE_API_KEY`
  2. 指定 `auth.json` 的 `APIROUTER_API_KEY`
  3. 指定 `auth.json` 的 `OPENAI_API_KEY`
- `auth.json` 路径支持自定义：
  - 启动参数：`--auth-json-path /path/to/auth.json`
  - 环境变量：`CODEX_AUTH_JSON_PATH=/path/to/auth.json`
- 可选入站鉴权：`INBOUND_BEARER`（默认关闭）
- 自动透传已支持的顶层缓存参数（当前：`prompt_cache_key`、`enable_caching`）到上游 Responses API
- 若请求未携带 `prompt_cache_key`，代理会自动注入稳定 key（可关闭）：
  - 基于 `system` 前缀（默认前 4096 字符）+ 前 6 条 `user/assistant` 片段（含 assistant 的 `tool_calls` 名称与参数）
  - 仅当 `user/assistant` 消息累计到 6 条后才注入（默认）

## 3) 安装与启动文档

安装说明已拆分为独立文档：

- Linux：`INSTALL_LINUX.md`
- Windows（非 uv 路径）：`INSTALL_WINDOWS.md`

两个文档都包含“安装后验证方法”（`scripts/smoke_test.py`）。

## 4) 目录

- `app.py`：主程序
- `requirements.txt`：依赖
- `scripts/smoke_test.py`：一键自测脚本
- `moacode-chat-proxy.service`：Linux systemd 服务示例
- `INSTALL_LINUX.md`：Linux 安装文档
- `INSTALL_WINDOWS.md`：Windows 安装文档（非 uv）

---

## 5) systemd 是什么？为什么要用？（Linux）

`systemd` 是 Linux 的服务管理器。用它可以让代理：

- 后台常驻运行（关掉 SSH 终端也不会停）
- 开机自动启动
- 异常时自动重启
- 统一查看状态和日志

### 5.1 安装/启用服务

```bash
sudo cp /root/moacode_chat_proxy/moacode-chat-proxy.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now moacode-chat-proxy
```

### 5.2 常用命令

```bash
sudo systemctl status moacode-chat-proxy      # 看状态
sudo systemctl restart moacode-chat-proxy     # 重启
sudo systemctl stop moacode-chat-proxy        # 停止
sudo systemctl start moacode-chat-proxy       # 启动
sudo journalctl -u moacode-chat-proxy -f      # 实时日志
```

### 5.3 修改配置后生效

每次修改 service 文件后都要执行：

```bash
sudo systemctl daemon-reload
sudo systemctl restart moacode-chat-proxy
```

---

## 6) 环境变量说明

- `PORT`：监听端口，默认 `5102`
- `MOACODE_API_KEY`：上游 key（最高优先级）
- `CODEX_AUTH_JSON_PATH`：`auth.json` 路径（例如 `/root/.codex/auth.json`）
- `INBOUND_BEARER`：开启入站鉴权时使用
- `AUTO_PROMPT_CACHE_KEY`：是否自动注入 `prompt_cache_key`（默认 `1`，设 `0` 关闭）
- `AUTO_PROMPT_CACHE_KEY_PREFIX_CHARS`：自动 key 使用的前缀字符数（默认 `4096`）
- `AUTO_PROMPT_CACHE_MIN_MESSAGES`：自动注入前要求的 `user/assistant` 消息数（默认 `6`）

入站鉴权示例（可选）：

```bash
INBOUND_BEARER=my_secret python app.py --auth-json-path /root/.codex/auth.json
```

客户端请求时必须带：

```http
Authorization: Bearer my_secret
```

---

## 7) 验证

### 7.1 健康检查

```bash
curl http://127.0.0.1:5102/health
```

### 7.2 一键 Python 自测（Windows/Linux 通用）

```bash
python scripts/smoke_test.py
```

如果启用了 `INBOUND_BEARER`，请带上：

```bash
python scripts/smoke_test.py --api-key your_inbound_bearer
```

### 7.3 Chat Completions

```bash
curl -sS -X POST http://127.0.0.1:5102/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{
    "model": "gpt-5.3-codex",
    "messages": [
      {"role": "user", "content": "你是谁"}
    ]
  }'
```
