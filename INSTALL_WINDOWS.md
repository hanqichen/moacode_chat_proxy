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
cd D:\Program\2026\moacode_chat_proxy
python .\app.py --auth-json-path "C:\Users\你的用户名\.codex\auth.jsonl"
```

`auth.jsonl` 路径通常在用户目录下的 `.codex` 文件夹内，大致位置是：

- `C:\Users\你的用户名\.codex\auth.jsonl`

如果你不确定用户名，也可以先在 PowerShell 里执行 `echo $env:USERPROFILE` 查看用户目录。

## 4) 验证是否成功

新开一个 PowerShell 后，先执行下面两条命令进入项目并激活虚拟环境：

```powershell
cd D:\Program\2026\moacode_chat_proxy
.\.venv\Scripts\Activate.ps1
```

然后执行自测脚本：

```powershell
python .\scripts\smoke_test.py
```

如果启用了 `INBOUND_BEARER`：

```powershell
python .\scripts\smoke_test.py --api-key 你的INBOUND_BEARER
```

看到 `[OK] 代理测试通过` 即表示安装与启动成功。
