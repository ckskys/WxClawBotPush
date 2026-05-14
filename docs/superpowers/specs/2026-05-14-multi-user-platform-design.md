# WxClawBotPush 多用户平台设计

**日期**: 2026-05-14
**范围**: 数据层改造 → 多用户平台 → Webhook 鉴权 → 消息模板 → 管理后台
**目标**: 将单用户推送工具升级为多用户多微信机器人平台

---

## 1. 数据库 Schema

### 表结构

```sql
-- 用户账号表
CREATE TABLE users (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    username        TEXT NOT NULL UNIQUE,
    password_hash   TEXT NOT NULL,
    is_admin        INTEGER DEFAULT 0,
    is_disabled     INTEGER DEFAULT 0,
    created_at      TEXT DEFAULT (datetime('now'))
);

-- 用户配置表（每用户独立的 bot 连接 + webhook 配置）
CREATE TABLE user_configs (
    user_id           INTEGER PRIMARY KEY REFERENCES users(id),
    base_url          TEXT DEFAULT 'https://ilinkai.weixin.qq.com',
    bot_token         TEXT,
    account_id        TEXT,
    sync_buf          TEXT,
    known_users       TEXT DEFAULT '[]',       -- JSON 数组
    context_tokens    TEXT DEFAULT '{}',        -- JSON 对象
    webhook_token     TEXT UNIQUE,
    message_template  TEXT DEFAULT '{{ title }}\n{{ text }}\n{{ link }}',
    updated_at        TEXT DEFAULT (datetime('now'))
);

-- 系统配置表（全局开关）
CREATE TABLE system_config (
    key   TEXT PRIMARY KEY,
    value TEXT
);

-- 推送日志表（摘要，不含完整消息内容）
CREATE TABLE push_logs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER REFERENCES users(id),
    target_user TEXT,
    status      TEXT,       -- ok / fail
    created_at  TEXT DEFAULT (datetime('now'))
);
```

### 首个管理员创建方式

- 环境变量 `ADMIN_USERNAME` + `ADMIN_PASSWORD`：启动时自动创建
- 命令行 `python -m wxclawbotpush.create_admin`：手动兜底
- 管理员已存在时跳过

---

## 2. 模块变更

```
wxclawbotpush/
├── app.py           # 修改：启动时初始化 DB + 自动创建管理员 + 注册路由
├── database.py      # 新增：SQLite 连接管理、建表、DAO 方法
├── auth.py          # 新增：密码哈希(bcrypt)、会话管理、注册/登录
├── config.py        # 重写：全局单文件 → 数据库读写，保留环境变量 fallback
├── client.py        # 重写：单实例单例 → user_id → ILinkClient 多实例池
├── webhook.py       # 修改：增加 token 鉴权，按用户分发
├── polling.py       # 修改：每用户独立轮询线程生命周期管理
├── template.py      # 新增：消息模板解析渲染
├── admin.py         # 大幅扩展：用户管理 + 系统配置 + 看板 + 用户设置
├── ilink/           # 不变
│   ├── __init__.py
│   ├── client.py    # 不变
│   └── models.py    # 不变
├── log_utils.py     # 不变
└── templates/
    ├── admin.html   # 重写：完整管理后台
    └── login.html   # 新增：登录/注册页面
```

### 模块职责

| 模块 | 职责 |
|------|------|
| `database.py` | 数据库连接、建表迁移、CRUD 封装 |
| `auth.py` | 密码哈希校验、简单 session token 签发验证、管理员初始化 |
| `config.py` | 全局 system_config 读写、用户级 user_configs 读写 |
| `client.py` | 多实例 ILinkClient 池：get_client(user_id)、recreate_client(user_id)、close_client(user_id) |
| `webhook.py` | token 鉴权中间件、按用户路由分发、消息模板渲染后广播 |
| `polling.py` | 每用户独立轮询线程的启动/停止，自动发现新联系人 |
| `template.py` | `render_template(template_str, data)` 递归解析 `{{ field.sub }}` |
| `admin.py` | 管理员：用户 CRUD、系统配置、状态看板、日志查看。用户：个人设置、token 管理 |
| `app.py` | FastAPI 实例化、路由注册、startup 事件 |

### 依赖关系

```
app.py
  ├── database.py        (无内部依赖)
  ├── auth.py            → database, config
  ├── config.py          → database
  ├── client.py          → config, ilink.client
  ├── webhook.py         → config, client, template
  ├── polling.py         → config, client, ilink.client
  ├── template.py        (无内部依赖)
  ├── admin.py           → config, client, database, auth, log_utils, polling
  └── log_utils.py       (无内部依赖)

ilink/
  ├── client.py          → models
  └── models.py          (无内部依赖)
```

---

## 3. Webhook 鉴权流程

```
POST /webhook?token=abc123
       ↓
  token 匹配 user_configs.webhook_token
       ↓ 无匹配 → 401 Unauthorized
       ↓ 匹配成功 → 获取该用户 bot 实例
       ↓
  消息模板渲染（如有）或 fallback parse_webhook_payload
       ↓
  _broadcast_to_users（该用户的 known_users）
       ↓
  记录 push_logs
       ↓
  {"success": true, "results": {...}}
```

### Token 来源优先级

1. URL 参数 `?token=xxx`
2. HTTP 头 `X-Token: xxx`

---

## 4. 消息模板

### 语法

- 占位符 `{{ field }}`，递归解析嵌套 `{{ alert.name }}`
- 缺失字段替换为空字符串
- 不在此次迭代引入条件语句

### 默认模板

```
{{ title }}\n{{ text }}\n{{ link }}
```

### Fallback

模板为空时使用现有 `parse_webhook_payload()` 的字段提取逻辑。

---

## 5. 多实例 Client 管理

```python
# client.py 改为多实例池
_clients: Dict[int, ILinkClient] = {}      # user_id → client
_qr_code_data: Dict[int, Dict] = {}        # user_id → qr data
```

- `get_client(user_id)` — 按用户获取或创建 ILinkClient
- `recreate_client(user_id, **kwargs)` — 重建指定用户的实例
- `close_client(user_id)` — 关闭指定用户的连接
- `close_all()` — 关闭所有连接（shutdown 用）

---

## 6. 路由结构

| 路径 | 方法 | 用途 | 鉴权 |
|------|------|------|------|
| `/webhook` | GET/POST | 用户推送入口 | webhook token |
| `/api/login` | POST | 登录 | 无 |
| `/api/register` | POST | 注册 | 全局开关控制 |
| `/api/logout` | POST | 退出 | session |
| `/api/admin/users` | GET/POST | 用户列表/创建 | session+admin |
| `/api/admin/users/{id}` | PUT/DELETE | 启用禁用/删除用户 | session+admin |
| `/api/admin/config` | GET/PUT | 全局系统配置 | session+admin |
| `/api/admin/stats` | GET | 统计看板 | session+admin |
| `/api/user/config` | GET/PUT | 个人设置 | session |
| `/api/user/qrcode` | GET | 个人扫码 | session |
| `/api/user/logs` | GET | 个人推送日志 | session |
| `/api/user/token` | POST | 重置 Webhook Token | session |
| `/` `/admin` | GET | 管理后台页面 | 无(页面) |

---

## 7. 不变项

- Docker 部署方式不变
- iLink API 调用方式不变
- `config.json` → DB 迁移自动执行，用户无感知
- 注册开关默认开放，管理员可关闭

## 8. 移除项

- 好友列表管理界面（iLink API 不支持主动拉取好友，`known_users` 仅后台自动累积）

---

## 9. 关键约束

- 用户间完全隔离：各自的 bot token、known_users、webhook token、推送日志
- 管理员查看推送日志摘要，不暴露完整消息内容
- 首个管理员由环境变量 `ADMIN_USERNAME`/`ADMIN_PASSWORD` 或命令行创建
- 支持 50+ 用户并发，每用户独立轮询线程
