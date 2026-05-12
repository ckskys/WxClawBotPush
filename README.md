# WxClawbotPush

基于 iLink/OpenClaw 协议的 Webhook 转 WeChat ClawBot 消息推送服务。接收任意 Webhook 消息，通过个人微信实时推送。

## 快速开始

### Docker 部署

```bash
# 拉取镜像
docker pull YOUR_USERNAME/wxclawbotpush:latest

# 启动容器
docker run -d \
    --name wxclawbotpush \
    --restart unless-stopped \
    -p 8099:8099 \
    -v $(pwd)/data:/data \
    -e TZ=Asia/Shanghai \
    YOUR_USERNAME/wxclawbotpush:latest
```

### Docker Compose 部署

```bash
# 1. 准备目录
mkdir wxclawbotpush && cd wxclawbotpush
mkdir data

# 2. 创建 docker-compose.yml
cat > docker-compose.yml << 'EOF'
version: "3.8"
services:
  wxclawbotpush:
    image: YOUR_USERNAME/wxclawbotpush:latest
    container_name: wxclawbotpush
    ports:
      - "8099:8099"
    volumes:
      - ./data:/data
    environment:
      - TZ=Asia/Shanghai
    restart: unless-stopped
EOF

# 3. 启动
docker compose up -d
```

### 自构建

```bash
git clone <repo-url>
cd wxclawbotpush
docker compose up -d --build
```

## 使用步骤

### 1. 扫码登录微信

打开浏览器访问 `http://你的IP:8099/admin`，点击「获取登录二维码」，用微信扫码完成登录。

### 2. 向机器人发送消息（自动记录用户）

用微信向 ClawBot 机器人发送任意消息（如「你好」），系统通过后台轮询自动记录你的微信用户 ID。
Webhook 消息将推送给所有已记录的微信用户。此步骤只需执行一次。

### 3. 发送 Webhook

向 `http://你的IP:8099/webhook` 发送 POST 请求，支持任意 JSON 格式，自动提取以下字段：

| 字段 | 说明 |
|------|------|
| title / subject / summary | 标题 |
| text / content / message / body | 正文 |
| link / url / href | 链接 |

支持 GET 方式发送纯文本：`http://你的IP:8099/webhook?msg=消息内容`

### 示例

```bash
# JSON 消息
curl -X POST http://192.168.1.100:8099/webhook \
  -H "Content-Type: application/json" \
  -d '{"title":"告警通知","text":"服务器 CPU 超过 90%"}'

# 纯文本消息
curl "http://192.168.1.100:8099/webhook?msg=备份任务已完成"
```

## 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| ILINK_BASE_URL | https://ilinkai.weixin.qq.com | iLink API 地址 |
| DATA_DIR | /data | 数据存储目录 |

## 管理页面

访问 `http://你的IP:8099/admin` 可进行：
- 扫码登录 / 退出登录
- 查看连接状态和已连接用户数
- 查看运行日志
- 获取 Webhook 地址
