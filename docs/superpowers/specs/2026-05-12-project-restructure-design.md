# WxClawbotPush 项目重构设计

**日期**: 2026-05-12  
**范围**: 文件结构、模块拆分、命名规范化，**不改变功能行为**

---

## 目标

将当前 `app/main.py`（550+ 行，混合 6 种职责）按关注点拆分为独立模块，统一文件命名和符号命名风格。

---

## 文件结构

### 当前

```
WxClawbotPush/
├── app/
│   ├── main.py              # FastAPI app, routes, config, logging, polling, login, admin page
│   ├── admin.html            # Admin page HTML template
│   └── ilink/
│       ├── __init__.py
│       └── client.py         # ILinkClient + ILinkIncomingMessage
├── data/
├── .github/workflows/
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
├── .env.example
├── .dockerignore
├── LICENSE
├── README.md
└── DOCKER_HUB.md
```

### 目标

```
WxClawbotPush/
├── wxclawbotpush/                # 原 app/，包名规范化
│   ├── __init__.py
│   ├── app.py                    # FastAPI 实例化、路由注册、startup 事件
│   ├── config.py                 # DEFAULT_CONFIG, load/save/get_config
│   ├── logging.py                # LogBufferHandler, setup_logging()
│   ├── webhook.py                # parse_webhook_payload, _broadcast_to_users, GET/POST 路由
│   ├── admin.py                  # 5 个 API 路由 + admin 页面路由
│   ├── polling.py                # 扫码监听 + 后台消息轮询
│   ├── ilink/
│   │   ├── __init__.py
│   │   ├── client.py             # ILinkClient
│   │   └── models.py             # IncomingMessage dataclass
│   └── templates/
│       └── admin.html
├── data/
├── .github/workflows/
├── Dockerfile                    # 调整 COPY 路径: wxclawbotpush/ → /app/
├── docker-compose.yml
├── pyproject.toml                # 新增，现代 Python 项目元数据
├── requirements.txt
├── .env.example
├── .dockerignore
├── LICENSE
└── README.md
```

---

## 命名变更

### 符号重命名

| 当前 | 新名称 | 原因 |
|------|--------|------|
| `extract_message_fields()` | `parse_webhook_payload()` | 更精确：解析 webhook 请求体 |
| `_do_send()` | `_broadcast_to_users()` | 明确表达向所有已知用户广播 |
| `_watch_login()` | `_poll_qr_code_status()` | 与 `_poll_incoming_messages` 风格一致 |
| `_poll_loop()` | `_poll_incoming_messages()` | 明确轮询目标 |
| `reset_client()` | `recreate_client()` | 实际是销毁旧实例创建新实例 |
| `BufferHandler` | `LogBufferHandler` | 避免命名过于泛化 |
| `_qrcode_data` | `_qr_code_data` | 可读性 |
| `api_status()` | `get_status()` | 路由注册时 path 不变 |
| `api_get_qrcode()` | `get_qr_code()` | 同上 |
| `api_qrcode_image()` | `get_qr_code_image()` | 同上 |
| `api_logout()` | `logout()` | 同上 |
| `api_logs()` | `get_logs()` | 同上 |
| `api_clear_logs()` | `clear_logs()` | 同上 |
| `ILinkIncomingMessage` | `IncomingMessage` | 已在 ilink 命名空间内，前缀冗余 |
| `webhook_get()` | `webhook_get_handler()` | 更明确 |
| `webhook_post()` | `webhook_post_handler()` | 更明确 |
| `admin_page()` | `serve_admin_page()` | 更明确表达行为 |

### 常量重命名

| 当前 | 新名称 |
|------|--------|
| `ADMIN_HTML` | 删除，改为读取 `<template>` 文件 |

---

## 模块职责

| 模块 | 估算行数 | 职责 |
|------|---------|------|
| `app.py` | ~30 | 导入各模块的路由，注册到 FastAPI app，startup 事件启动轮询 |
| `config.py` | ~50 | `DEFAULT_CONFIG`, `load_config()`, `save_config()`, `get_config()` |
| `logging.py` | ~40 | `LogBufferHandler`, `setup_logging()` 返回 `(logger, log_buffer, buffer_lock)` |
| `webhook.py` | ~100 | `parse_webhook_payload()`, `_broadcast_to_users()`, 2 个路由处理器 |
| `admin.py` | ~90 | 6 个路由（5 个 API + 1 个页面） |
| `polling.py` | ~130 | `start_polling()`, `stop_polling()`, `_poll_incoming_messages()`, `_poll_qr_code_status()` |
| `ilink/client.py` | ~300 | ILinkClient（内部方法名也规范化） |
| `ilink/models.py` | ~15 | `IncomingMessage` dataclass |

---

## 依赖关系

```
app.py
  ├── config.py          (无内部依赖)
  ├── logging.py         (无内部依赖)
  ├── webhook.py         → config, ilink.client
  ├── admin.py           → config, ilink.client, logging
  └── polling.py         → config, ilink.client, logging

ilink/
  ├── client.py          → models
  └── models.py          (无内部依赖)
```

各模块通过导入 `config` 模块的 `get_config` / `save_config` 和 `ilink.client` 的 `ILinkClient` 来协作，无循环依赖。

---

## 配套调整

1. **Dockerfile**：`COPY wxclawbotpush/ /app/`（原 `COPY app/ /app/`）
2. **docker-compose.yml**：无需改动
3. **pyproject.toml**：新增，声明项目元数据
4. **DOCKER_HUB.md**：删除，内容已复制到 Docker Hub 描述
5. **.gitignore**：新增，排除 `data/`, `__pycache__/`, `.env`

---

## 不变项

- 所有 HTTP 路由路径不变
- `config.json` 格式不变
- Docker 镜像外部接口不变
- 功能行为完全不变
