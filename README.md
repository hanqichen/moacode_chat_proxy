# moacode_chat_proxy

把 `POST /v1/chat/completions` 转换为上游 moacode 的 `POST /v1/responses`，用于兼容只会调用 Chat Completions 的客户端（如 OpenClaw）。

## 1) 功能

- 对外接口：`POST /v1/chat/completions`
- 健康检查：`GET /health` -> `{"status":"ok"}`
- 自动读取上游 key（优先级）：
  1. `MOACODE_API_KEY`
  2. 指定 `auth.json` 里的 `APIROUTER_API_KEY`
  3. 指定 `auth.json` 里的 `OPENAI_API_KEY`
- `auth.json` 路径支持自定义：
  - 启动参数：`--auth-json-path /path/to/auth.json`
  - 环境变量：`CODEX_AUTH_JSON_PATH=/path/to/auth.json`
- 可选入站鉴权：`INBOUND_BEARER`（默认关闭）

## 2) 目录

- `app.py`：主程序
- `requirements.txt`：依赖
- `moacode-chat-proxy.service`：Linux systemd 服务示例

---

## 3) Linux 启动（推荐：使用 uv）

### 3.1 安装 uv（如果没有）

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

安装后可执行文件通常在 `~/.local/bin/uv`。如果 `uv` 命令找不到，可先执行：

```bash
export PATH="$HOME/.local/bin:$PATH"
```

### 3.2 创建环境并安装依赖

```bash
cd /root/moacode_chat_proxy
uv venv .venv
source .venv/bin/activate
uv pip install -r requirements.txt
```

### 3.3 启动

```bash
python app.py --auth-json-path /root/.codex/auth.json
```

默认监听 `0.0.0.0:5102`。可改端口：

```bash
PORT=5200 python app.py --auth-json-path /root/.codex/auth.json
```

---

## 4) Linux 启动（不使用 uv，给没有 uv 的用户）

### 4.1 系统没有 `venv` / `pip` 时先安装

Debian/Ubuntu:

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip
```

CentOS/RHEL/Alma/Rocky:

```bash
sudo dnf install -y python3 python3-pip
```

### 4.2 创建环境并安装依赖

```bash
cd /root/moacode_chat_proxy
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

### 4.3 启动

```bash
python app.py --auth-json-path /root/.codex/auth.json
```

---

## 5) Windows 启动（PowerShell，给新手）

> Windows 不支持 systemd，Windows 上建议先手工运行，确认可用后再考虑“任务计划程序”做开机自启。

### 5.1 安装 Python（如果没有）

- 打开 PowerShell（管理员）执行：

```powershell
winget install Python.Python.3.11
```

安装后关闭并重新打开 PowerShell，验证：

```powershell
py --version
```

### 5.2 获取项目

把 `moacode_chat_proxy` 目录放到例如：`C:\moacode_chat_proxy`。

### 5.3 方式 A：用 uv（推荐）

安装 uv：

```powershell
powershell -ExecutionPolicy Bypass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

创建环境并安装依赖：

```powershell
cd C:\moacode_chat_proxy
uv venv .venv
.\.venv\Scripts\Activate.ps1
uv pip install -r requirements.txt
```

启动：

```powershell
$env:PORT="5102"
python .\app.py --auth-json-path "$env:USERPROFILE\.codex\auth.json"
```

### 5.4 方式 B：不用 uv

```powershell
cd C:\moacode_chat_proxy
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python .\app.py --auth-json-path "$env:USERPROFILE\.codex\auth.json"
```

---

## 6) systemd 是什么？为什么要用？（Linux）

`systemd` 是 Linux 的服务管理器。用它可以让你的代理：

- 后台常驻运行（你关掉 SSH 终端也不会停）
- 开机自动启动
- 异常时自动重启
- 统一查看状态和日志

### 6.1 安装/启用服务

```bash
sudo cp /root/moacode_chat_proxy/moacode-chat-proxy.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now moacode-chat-proxy
```

### 6.2 常用命令

```bash
sudo systemctl status moacode-chat-proxy      # 看状态
sudo systemctl restart moacode-chat-proxy     # 重启
sudo systemctl stop moacode-chat-proxy        # 停止
sudo systemctl start moacode-chat-proxy       # 启动
sudo journalctl -u moacode-chat-proxy -f      # 实时日志
```

### 6.3 修改配置后生效

每次修改 service 文件后都要执行：

```bash
sudo systemctl daemon-reload
sudo systemctl restart moacode-chat-proxy
```

---

## 7) 环境变量说明

- `PORT`：监听端口，默认 `5102`
- `MOACODE_API_KEY`：上游 key（最高优先级）
- `CODEX_AUTH_JSON_PATH`：`auth.json` 路径（例如 `/root/.codex/auth.json`）
- `INBOUND_BEARER`：开启入站鉴权时使用

入站鉴权示例（可选）：

```bash
INBOUND_BEARER=my_secret python app.py --auth-json-path /root/.codex/auth.json
```

客户端请求时必须带：

```http
Authorization: Bearer my_secret
```

---

## 8) curl 验证

### 8.1 健康检查

```bash
curl http://127.0.0.1:5102/health
```

### 8.2 Chat Completions

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
