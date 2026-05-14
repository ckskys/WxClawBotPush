from fastapi import FastAPI

from log_utils import setup_logging
from config import load_config, get_config
from polling import start_polling
from webhook import router as webhook_router
from admin import router as admin_router

logger = setup_logging()

app = FastAPI(title="WxClawbotPush", description="Webhook to WeChat ClawBot", version="1.0.0")
app.include_router(webhook_router)
app.include_router(admin_router)


@app.on_event("startup")
def startup():
    load_config()
    if get_config().get("bot_token"):
        start_polling()
    logger.info("WxClawbotPush 启动完成")
