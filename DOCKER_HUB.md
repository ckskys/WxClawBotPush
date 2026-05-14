# WxClawbotPush

Webhook 转 WeChat ClawBot 多用户推送平台。基于 iLink/OpenClaw 协议，支持多用户独立扫码登录、Webhook Token 鉴权、消息模板。

## 快速启动

```bash
docker run -d \
    --name wxclawbotpush \
    --restart unless-stopped \
    -p 8000:8000 \
    -v $(pwd)/data:/data \
    -e ADMIN_USERNAME=admin \
    -e ADMIN_PASSWORD=your_password \
    ckskys/wxclawbotpush:latest
```

启动后访问 `http://你的IP:8000/admin`，用设置的管理员账号登录。

## Docker Compose

```yaml
services:
  wxclawbotpush:
    image: ckskys/wxclawbotpush:latest
    container_name: wxclawbotpush
    ports:
      - "8000:8000"
    volumes:
      - ./data:/data
    environment:
      - ADMIN_USERNAME=admin
      - ADMIN_PASSWORD=your_password
    restart: unless-stopped
```

## 环境变量

| 变量 | 必填 | 默认值 | 说明 |
|------|------|--------|------|
| `ADMIN_USERNAME` | 推荐 | - | 首个管理员用户名 |
| `ADMIN_PASSWORD` | 推荐 | - | 首个管理员密码 |
| `ILINK_BASE_URL` | 否 | https://ilinkai.weixin.qq.com | iLink API |
| `WEBHOOK_PORT` | 否 | 8000 | 服务端口 |
| `DATA_DIR` | 否 | /data | 数据目录 |

## 使用流程

1. **登录** — 浏览器访问 `http://IP:8000/admin`，登录或注册账号
2. **扫码** — 在个人设置点击「扫码登录」，用微信扫码绑定机器人
3. **激活联系人** — 让需要接收推送的微信好友向机器人发送一条消息
4. **推送** — 使用个人 Webhook Token 发送消息

## Webhook 推送

```bash
# POST JSON（自动应用消息模板）
curl -X POST "http://IP:8000/webhook?token=YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"title":"告警","text":"CPU 超过 90%"}'

# GET 纯文本
curl "http://IP:8000/webhook?token=YOUR_TOKEN&msg=备份完成"
```

## 消息模板

在管理面板配置，支持 `{{ field }}` 和 `{{ alert.name }}` 嵌套占位符，缺失字段自动替换为空。

默认模板：
```
{{ title }}
{{ text }}
{{ link }}
```

## 管理员功能

- 用户管理（创建、启用/禁用、删除）
- 注册开关控制
- 系统看板（用户数、消息量统计）

## 源码

https://github.com/ckskys/WxClawBotPush
