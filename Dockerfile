FROM python:3.11-slim

ENV TZ=Asia/Shanghai
ENV WEBHOOK_PORT=8000
ENV DATA_DIR=/data

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY wxclawbotpush/ /app/

RUN mkdir -p /data

EXPOSE 8000

CMD ["sh", "-c", "exec uvicorn app:app --host 0.0.0.0 --port ${WEBHOOK_PORT}"]
