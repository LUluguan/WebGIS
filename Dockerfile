FROM python:3.13-slim

WORKDIR /app

# 先装依赖(利用镜像层缓存)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu

# 拷贝应用(大文件由 .dockerignore 排除)
COPY . .

EXPOSE 8001
CMD ["python", "-m", "uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8001"]
