FROM python:3.11-slim

WORKDIR /app

# 设置环境变量
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# 安装依赖
COPY requirements.txt .
RUN apt-get update && \
    apt-get install -y --no-install-recommends gcc libc6-dev && \
    pip install --no-cache-dir -r requirements.txt && \
    apt-get purge -y --auto-remove gcc libc6-dev && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# 创建必要的目录
RUN mkdir -p logs data

# 复制代码
COPY . .

# 配置日志目录权限
RUN chmod -R 755 logs data

# 运行机器人
CMD ["python", "main.py"] 