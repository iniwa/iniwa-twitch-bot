FROM python:3.9-slim

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
RUN mkdir -p /app/data && chown -R 1000:1000 /app

EXPOSE 8501

# 日本時間設定
ENV TZ=Asia/Tokyo

USER 1000:1000

CMD ["python", "app.py"]
