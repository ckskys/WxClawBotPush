# WxClawbotPush

基于 iLink/OpenClaw 协议的 Webhook 转微信 ClawBot 消息推送服务。接收任意 Webhook 消息，通过个人微信实时推送。

## 快速开始

### Docker 部署

```bash
docker run -d \
    --name wxclawbotpush \
    --restart unless-stopped \
    -p 8099:8099 \
    -v $(pwd)/data:/data \
    -e TZ=Asia/Shanghai \
    ckskys/wxclawbotpush:latest
```

### Docker Compose 部署

```yaml
services:
  wxclawbotpush:
    image: ckskys/wxclawbotpush:latest
    container_name: wxclawbotpush
    ports:
      - "8099:8099"
    volumes:
      - ./data:/data
    environment:
      - TZ=Asia/Shanghai
    restart: unless-stopped
```

```bash
docker compose up -d
```

## 使用方法

**1. 扫码登录** — 浏览器访问 `http://你的IP:8099/admin`，点击「获取登录二维码」，用微信扫码。

**2. 激活推送** — 扫码后，用微信向机器人发送任意消息（如「你好」），系统自动记录你的微信账号。只需一次。

**3. 发送消息** — 向 `/webhook` 端点发送 POST 请求：

```bash
# JSON 消息
curl -X POST http://你的IP:8099/webhook \
  -H "Content-Type: application/json" \
  -d '{"title":"告警通知","text":"服务器 CPU 超过 90%"}'

# 纯文本
curl "http://你的IP:8099/webhook?msg=备份任务已完成"
```

## 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `ILINK_BASE_URL` | `https://ilinkai.weixin.qq.com` | iLink API 地址 |
| `DATA_DIR` | `/data` | 配置文件存储路径 |
| `WEBHOOK_PORT` | `8099` | 服务端口 |

## 数据持久化

配置文件 `config.json` 存储在 `/data` 目录，包含登录信息、已知用户列表等，建议挂载到宿主机。

## 适用场景

- 群晖 / NAS 系统通知转发到微信
- 监控告警（Prometheus、Grafana、Uptime Kuma）
- CI/CD 流水线结果通知
- 任何支持 Webhook 的服务推送到微信
