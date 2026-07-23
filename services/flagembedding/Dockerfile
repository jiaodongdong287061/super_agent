FROM python:3.12-slim

WORKDIR /app

# 先装依赖（含 torch GPU 版，作为中间步骤）
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 覆盖为 CPU 版 PyTorch，缩小镜像体积
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu

# 预下载模型到本地路径（跳过 imgs 目录，hf-mirror 对 .DS_Store 返回 403）
ENV HF_ENDPOINT=https://hf-mirror.com
RUN huggingface-cli download BAAI/bge-m3 --exclude "imgs/*" --local-dir /models/BAAI/bge-m3
RUN huggingface-cli download BAAI/bge-reranker-v2-m3 --exclude "imgs/*" --local-dir /models/BAAI/bge-reranker-v2-m3

# 设置默认模型为本地路径（运行时可被环境变量覆盖）
ENV FE_EMBED_MODEL=/models/BAAI/bge-m3
ENV FE_RERANK_MODEL=/models/BAAI/bge-reranker-v2-m3

COPY main.py .

EXPOSE 8001

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8001"]
