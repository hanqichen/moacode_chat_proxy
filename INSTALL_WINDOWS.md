# Windows 安装与启动（非 uv）

本文档用于 Windows PowerShell 环境安装和运行 `moacode_chat_proxy`，全程不依赖 `uv`。

## 1) 安装 Python 3.11

管理员 PowerShell:

```powershell
winget install Python.Python.3.11
```

重开 PowerShell 后确认：

```powershell
py --version
```

## 2) 创建虚拟环境并安装依赖

```powershell
cd D:\Program\2026\moacode_chat_proxy
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

## 3) 启动服务

```powershell
$env:PORT="5102"
python .\app.py --auth-json-path "$env:USERPROFILE\.codex\auth.json"
```

## 4) 验证是否成功

新开一个 PowerShell（同样先激活 `.venv`）后执行：

```powershell
python .\scripts\smoke_test.py
```

如果启用了 `INBOUND_BEARER`：

```powershell
python .\scripts\smoke_test.py --api-key 你的INBOUND_BEARER
```

看到 `[OK] 代理测试通过` 即表示安装与启动成功。

