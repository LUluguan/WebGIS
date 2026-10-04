FROM python:3.13-slim

WORKDIR /app

# 中文字体(专题图 PNG 制图需要, slim 镜像默认无任何字体)
RUN apt-get update && apt-get install -y --no-install-recommends fonts-wqy-microhei \
    && rm -rf /var/lib/apt/lists/*

# 先装依赖(利用镜像层缓存)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu

# 拷贝应用(大文件由 .dockerignore 排除)
COPY . .

EXPOSE 8001
CMD ["python", "-m", "uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8001"]
