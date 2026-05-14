# WxClawbotPush

基于 iLink/OpenClaw 协议的 Webhook 转 WeChat ClawBot 多用户推送平台。每个用户独立扫码登录自己的微信机器人，通过 Webhook Token 鉴权实现消息推送。

## 快速开始

### Docker 部署

```bash
docker run -d \
    --name wxclawbotpush \
    --restart unless-stopped \
    -p 8000:8000 \
    -v $(pwd)/data:/data \
    -e TZ=Asia/Shanghai \
    -e ADMIN_USERNAME=admin \
    -e ADMIN_PASSWORD=your_password \
    ckskys/wxclawbotpush:latest
```

### Docker Compose

```bash
# 1. 创建目录
mkdir wxclawbotpush && cd wxclawbotpush
mkdir data

# 2. 创建 docker-compose.yml
cat > docker-compose.yml << 'EOF'
services:
  wxclawbotpush:
    image: ckskys/wxclawbotpush:latest
    container_name: wxclawbotpush
    ports:
      - "8000:8000"
    volumes:
      - ./data:/data
    environment:
      - TZ=Asia/Shanghai
      - ADMIN_USERNAME=admin
      - ADMIN_PASSWORD=your_password
    restart: unless-stopped
EOF

# 3. 启动
docker compose up -d
```

### 自构建

```bash
git clone https://github.com/ckskys/WxClawBotPush.git
cd WxClawbotPush
docker compose up -d --build
```

### 本地部署

```bash
# 1. 克隆项目
git clone https://github.com/ckskys/WxClawBotPush.git
cd WxClawbotPush

# 2. 配置环境变量
cp .env.example .env
# 编辑 .env 修改管理员密码

# 3. 安装依赖
pip install -r requirements.txt

# 4. 启动
source .env
bash start.sh
```

或使用启动脚本一键启动：

```bash
ADMIN_USERNAME=admin ADMIN_PASSWORD=your_pass bash start.sh
```

## 使用步骤

### 1. 访问管理页面

打开浏览器访问 `http://你的IP:8000/admin`，首次访问会重定向到登录页。

### 2. 注册账号（或使用管理员账号）

如果开放注册，在登录页切换到「注册」tab 创建账号。管理员账号由环境变量 `ADMIN_USERNAME` / `ADMIN_PASSWORD` 在部署时自动创建。

### 3. 扫码登录微信

登录后，在「个人设置」中点击「扫码登录」，用微信扫描二维码。扫码成功后系统自动启动后台轮询，发现给机器人发过消息的微信好友。

### 4. 让接收者向机器人发消息

需要接收推送的微信好友，必须先向机器人发送任意消息。系统通过轮询自动记录其用户 ID。

### 5. 发送 Webhook 推送

使用个人设置中显示的 Webhook Token 发送消息：

```bash
# JSON 消息（支持消息模板渲染）
curl -X POST http://你的IP:8000/webhook?token=YOUR_TOKEN \
  -H "Content-Type: application/json" \
  -d '{"title":"告警通知","text":"服务器 CPU 超过 90%","link":"https://grafana.example.com"}'

# 纯文本消息
curl "http://你的IP:8000/webhook?token=YOUR_TOKEN&msg=备份任务已完成"
```

推送地址: `GET/POST /webhook?token=你的Token`

## 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `ADMIN_USERNAME` | - | 首个管理员用户名 |
| `ADMIN_PASSWORD` | - | 首个管理员密码 |
| `ILINK_BASE_URL` | https://ilinkai.weixin.qq.com | iLink API 地址 |
| `DATA_DIR` | /data | 数据存储目录（SQLite 数据库） |

## 消息模板

用户可在管理面板配置消息模板，支持 `{{ field }}` 占位符和嵌套字段 `{{ alert.name }}`。缺失字段自动替换为空字符串。

默认模板：`{{ title }}\n{{ text }}\n{{ link }}`

示例：

```
⚠️ 告警：{{ alert.name }}
级别：{{ alert.severity }}
时间：{{ timestamp }}
```

## 管理员功能

管理员登录后可访问：
- **系统看板** — 总用户数、今日/总消息发送量
- **开放/关闭注册** — 一键控制注册开关
- **用户管理** — 创建、启用/禁用、删除用户

## 管理页面

访问 `http://你的IP:8000/admin`：
- 扫码登录 / 断开微信
- 查看 Webhook Token 和推送地址
- 编辑消息模板

## 命令行工具

```bash
# 手动创建管理员
python -m wxclawbotpush.create_admin <用户名> <密码>
```
