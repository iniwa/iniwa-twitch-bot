# Iniwa's Twitch Bot

Twitch 配信のための多機能 Bot & 管理ダッシュボード。

視聴者管理、配信アナリティクス、チャンネルポイント予想、アーカイブ自動ダウンロードを Web GUI から一元管理できます。Docker コンテナとして Raspberry Pi 4 (arm64) 上で動作します。

## 主な機能

### ダッシュボード

- 視聴者のリアルタイム表示（滞在時間・コメント数・訪問回数）
- 視聴者ごとのメモ保存
- 配信プリセット（タイトル・カテゴリー・タグ）のワンクリック適用
- Shoutout (SO) コマンドの GUI 実行

### アナリティクス

- 配信ごとの同接推移・コメント速度・スタンプ使用率のグラフ表示
- カレンダー形式の配信履歴
- フォロワー増減・視聴者の参加頻度・累計視聴時間の追跡
- チャットログ・ビッツ・サブスク・ポイント使用ログの保存と閲覧

### チャット Bot

- IRC 経由の Twitch チャット接続
- 定期コメント・キーワード反応の自動チャットルール
- チャンネルポイント予想の作成・確定・キャンセル

### VOD アーカイブ

- yt-dlp + ffmpeg による配信アーカイブの自動ダウンロード
- ダウンロード状況の確認、再ダウンロード、削除

## 技術スタック

| 項目 | 技術 |
|------|------|
| バックエンド | Python 3.9 / Flask |
| フロントエンド | Jinja2 テンプレート / HTML / CSS / JS |
| チャット接続 | Twitch IRC (WebSocket) |
| API | Twitch Helix API |
| 動画DL | yt-dlp / ffmpeg |
| インフラ | Docker / GitHub Actions → GHCR |
| 実行環境 | Raspberry Pi 4 (linux/arm64) |

## セットアップ

### 必要なもの

- Docker Engine & Docker Compose
- Twitch Developer Console で取得した API 情報:
  - Client ID
  - Access Token（Bot アカウントの User Access Token）
  - Broadcaster ID（配信者のユーザー ID）
  - Bot User ID（Bot アカウントのユーザー ID）

### 起動

```bash
git clone https://github.com/iniwa/iniwa-twitch-bot.git
cd iniwa-twitch-bot
docker compose up -d --build
```

ブラウザで `http://localhost:8501` にアクセスし、初回設定で Twitch API 情報を入力してください。

### compose.yaml

動画の保存先を環境に合わせて変更してください。

```yaml
volumes:
  - /home/iniwa/docker/twitch-bot/data:/app/data
  - /path/to/your/video/dir:/app/downloads  # 保存先を変更
```

### 停止

```bash
docker compose down
```

## ディレクトリ構成

```
app.py                  # Flask アプリケーション エントリーポイント
config.py               # 設定管理・グローバル状態
routes/                 # ルーティング
  dashboard.py          #   ダッシュボード
  analytics.py          #   アナリティクス
  predictions.py        #   チャンネルポイント予想
  presets.py            #   配信プリセット
  rules.py              #   自動チャットルール
  viewers.py            #   視聴者管理
  vod.py                #   VOD 管理
  settings.py           #   設定
  filters.py            #   Jinja2 フィルター
services/               # バックグラウンド処理
  irc.py                #   Twitch IRC 接続
  twitch_api.py         #   Twitch Helix API ラッパー
  workers.py            #   バックグラウンドワーカー
  predictions.py        #   予想 API 操作
  download.py           #   VOD ダウンロード
  storage.py            #   データ永続化
templates/              # Jinja2 HTML テンプレート
static/                 # CSS / JS / 画像
data/                   # 永続化データ (実行時に生成)
  config.json           #   設定ファイル
  viewers.json          #   視聴者データ
  history/              #   配信ログ (.jsonl)
downloads/              # VOD 保存先 (compose.yaml でマウント)
```

## デプロイ

`main` ブランチへの push で GitHub Actions が自動実行され、`linux/amd64` と `linux/arm64` のマルチアーキテクチャイメージが `ghcr.io/iniwa/iniwa-twitch-bot:latest` に公開されます。

Portainer の Stack Web Editor にて compose.yaml を貼り付けてデプロイします。

## ライセンス

[MIT License](LICENSE)

## 免責事項

個人利用を目的としています。Twitch API の利用規約に従って使用してください。アーカイブのダウンロード機能は、自身のチャンネルのバックアップ目的での使用を推奨します。
