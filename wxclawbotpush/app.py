"""FastAPI 应用工厂，负责组装路由、生命周期管理和初始化。"""
import os
from fastapi import FastAPI

from database import init_db, close_db
from auth import create_admin
from log_utils import setup_logging
from webhook import router as webhook_router
from admin import router as admin_router

logger = setup_logging()

app = FastAPI(title="WxClawbotPush", description="Multi-user Webhook to WeChat ClawBot", version="2.0.0")
app.include_router(webhook_router)
app.include_router(admin_router)


@app.on_event("startup")
def startup():
    """应用启动时执行：初始化数据库、创建管理员账号。"""
    init_db()

    admin_user = os.environ.get("ADMIN_USERNAME")
    admin_pass = os.environ.get("ADMIN_PASSWORD")
    if admin_user and admin_pass:
        uid = create_admin(admin_user, admin_pass)
        if uid:
            logger.info(f"管理员已就绪: user_id={uid}")

    logger.info("WxClawbotPush 启动完成")


@app.on_event("shutdown")
def shutdown():
    """应用关闭时执行：停止所有轮询、关闭客户端连接和数据库。"""
    from polling import stop_all_polling
    from client import close_all
    stop_all_polling()
    close_all()
    close_db()
    logger.info("WxClawbotPush 已关闭")
