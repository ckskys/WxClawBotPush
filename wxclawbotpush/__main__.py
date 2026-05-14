"""模块入口：通过 uvicorn 启动 FastAPI 应用。"""
import uvicorn


def main():
    """启动 HTTP 服务，监听所有网络接口的 8000 端口。"""
    uvicorn.run("app:app", host="0.0.0.0", port=8000)


if __name__ == "__main__":
    main()
