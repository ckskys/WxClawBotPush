FROM python:3.11-slim

ENV TZ=Asia/Shanghai
ENV WEBHOOK_PORT=8099
ENV DATA_DIR=/data

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ /app/

RUN mkdir -p /data

EXPOSE 8099

CMD ["sh", "-c", "exec uvicorn main:app --host 0.0.0.0 --port ${WEBHOOK_PORT}"]
