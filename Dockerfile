FROM python:3.12-slim

ARG UID=1000
ARG GID=1000

WORKDIR /app

# 1. システムパッケージ (ffmpeg等) のインストール
RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# 2. Pythonライブラリのインストール
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 3. アプリケーションのコピー
COPY . .

# 4. データディレクトリ作成 & 所有者設定
RUN mkdir -p /app/data && chown -R ${UID}:${GID} /app

EXPOSE 8501

# 日本時間設定
ENV TZ=Asia/Tokyo

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8501/')" || exit 1

USER ${UID}:${GID}

CMD ["gunicorn", "--bind", "0.0.0.0:8501", "--workers", "1", "--threads", "4", "app:app"]
