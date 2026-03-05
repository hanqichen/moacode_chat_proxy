# Linux 安装与启动

本文档用于 Linux 环境安装和运行 `moacode_chat_proxy`。

## 1) 准备依赖

Debian/Ubuntu:

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip
```

CentOS/RHEL/Alma/Rocky:

```bash
sudo dnf install -y python3 python3-pip
```

## 2) 创建虚拟环境并安装依赖

```bash
cd /root/moacode_chat_proxy
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

## 3) 启动服务

```bash
python app.py --auth-json-path /root/.codex/auth.json
```

默认端口 `5102`，改端口示例：

```bash
PORT=5200 python app.py --auth-json-path /root/.codex/auth.json
```

## 4) 验证是否成功

推荐直接运行自测脚本：

```bash
python scripts/smoke_test.py
```

如果启用了 `INBOUND_BEARER`：

```bash
python scripts/smoke_test.py --api-key your_inbound_bearer
```

看到 `[OK] 代理测试通过` 即表示安装与启动成功。

