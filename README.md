# Iniwa's Twitch Bot

Twitch 配信のための多機能 Bot & 管理ダッシュボード。

視聴者管理、自動チャット、チャンネルポイント予想、配信アナリティクス、VOD アーカイブを Web GUI から一元管理できます。Raspberry Pi 4 (arm64) 上の Docker コンテナとして動作し、Portainer 経由でデプロイします。

## 機能

### ダッシュボード (`/`)

- リアルタイム視聴者リスト (滞在時間・訪問回数・フォロー状態)
- 視聴者ごとのメモ保存
- Shoutout (SO) の GUI 実行 (ボタン / ユーザーID入力)
- 配信ステータスの表示 (LIVE/OFF・現在のカテゴリー・視聴者数)
- イベントフィード (サブスク・ギフトサブ・レイド・ビッツ・フォロー)
- ルール稼働状況のモニター (次回実行タイマー・コメント条件)
- 操作ログの表示

### 配信プリセット

- タイトル・カテゴリー・タグのワンクリック適用
- ゲーム名の検索 (Twitch API 連携)
- Tweet 用タグの設定

### 自動チャットルール

- 定期コメントルール (インターバル + 最低コメント数条件)
- ゲーム別のルール分岐 (`All` / `Default` / 特定ゲーム)
- ルールの有効・無効管理

### チャンネルポイント予想

- 予想の作成 (タイトル・選択肢・制限時間)
- 予想プリセットの保存・呼び出し
- 結果確定・キャンセル操作

### アナリティクス (`/analytics`)

- 配信ごとの同接推移・コメント速度・スタンプ使用率のグラフ
- カレンダー形式の配信履歴一覧
- フォロワー推移・視聴者の累計視聴時間・参加頻度の追跡
- チャットログ・ビッツ・サブスク・ポイント使用ログの閲覧

### 視聴者履歴

- 全視聴者の累計データ (訪問回数・総視聴時間・コメント数・ビッツ・サブ状態・ギフト数・連続訪問)
- ソート可能なテーブル
- フォロワーリストの表示・手動同期

### VOD アーカイブ

- yt-dlp + ffmpeg による配信アーカイブのダウンロード
- 一括ダウンロード・個別ダウンロード
- ダウンロード進捗表示・キャンセル・削除
- 配信終了時の自動ダウンロード
- Twitch VOD 履歴の同期

## 技術スタック

| 項目 | 技術 |
|------|------|
| バックエンド | Python 3.9 / Flask |
| フロントエンド | Jinja2 / Vanilla JS / CSS |
| チャット接続 | Twitch IRC (SSL ソケット) |
| API | Twitch Helix API |
| 動画 DL | yt-dlp / ffmpeg |
| データ | JSON ファイル (config / viewers / stream index) |
| インフラ | Docker / GitHub Actions / GHCR / Portainer |
| 実行環境 | Raspberry Pi 4 (linux/arm64) |

## セットアップ

### 必要なもの

- Docker Engine
- Twitch Developer Console で取得した以下の情報:
  - Client ID
  - Access Token (Bot アカウントの User Access Token)
  - Broadcaster ID (配信者のユーザー ID)
  - Bot User ID (Bot アカウントのユーザー ID)
  - Channel Name (配信チャンネル名)

### 起動

```bash
git clone https://github.com/iniwa/iniwa-twitch-bot.git
cd iniwa-twitch-bot
docker compose up -d --build
```

ブラウザで `http://<ホスト>:8501` にアクセスし、設定画面から Twitch API 情報を入力してください。

### compose.yaml

```yaml
services:
  twitch-bot:
    image: ghcr.io/iniwa/iniwa-twitch-bot:latest
    container_name: twitch-bot
    restart: unless-stopped
    network_mode: "host"
    volumes:
      - /home/iniwa/docker/twitch-bot/data:/app/data
      - /path/to/video/dir:/app/downloads  # VOD 保存先
    environment:
      - TZ=Asia/Tokyo
    user: "1000:1000"
```

`/app/data` に設定ファイルと視聴者データが保存されます。`/app/downloads` は VOD の保存先です。環境に合わせてホスト側パスを変更してください。

## ディレクトリ構成

```
app.py                  # エントリーポイント (Flask)
config.py               # 設定管理・共有状態・スレッド安全カウンター
routes/
  __init__.py           #   Blueprint 登録
  dashboard.py          #   ダッシュボード・ステータス API
  analytics.py          #   アナリティクス
  predictions.py        #   チャンネルポイント予想
  presets.py            #   配信プリセット
  rules.py              #   自動チャットルール
  viewers.py            #   視聴者管理 (メモ・SO)
  vod.py                #   VOD ダウンロード管理
  settings.py           #   設定
  filters.py            #   Jinja2 カスタムフィルター
services/
  irc.py                #   Twitch IRC 接続 (SSL ソケット)
  twitch_api.py         #   Twitch Helix API クライアント
  workers.py            #   バックグラウンドワーカー (視聴者監視・ルール実行・統計)
  predictions.py        #   予想 API 操作
  download.py           #   VOD ダウンロード (yt-dlp)
  storage.py            #   ファイル I/O・インデックス管理
templates/
  base.html             #   ベーステンプレート
  dashboard.html        #   ダッシュボード
  analytics_list.html   #   配信一覧
  analytics_detail.html #   配信詳細
  partials/             #   パーシャルテンプレート (カード・モーダル)
static/
  css/                  #   スタイルシート
  js/                   #   クライアント JS (ポーリング・UI)
  img/                  #   favicon 等
data/                   #   永続化データ (実行時に生成)
  config.json           #     設定
  viewers.json          #     視聴者データ
  stream_index.json     #     配信履歴インデックス
  history/              #     配信ごとの分間統計 (.jsonl)
downloads/              #   VOD 保存先 (compose でマウント)
```

## デプロイ

`main` ブランチへの push で GitHub Actions が自動実行されます。

1. `linux/amd64` + `linux/arm64` のマルチアーキテクチャイメージをビルド
2. `ghcr.io/iniwa/iniwa-twitch-bot:latest` に push
3. Portainer の Stack Web Editor で compose.yaml を貼り付けてデプロイ

手動実行 (`workflow_dispatch`) にも対応しています。

## ライセンス

[MIT License](LICENSE)

## 免責事項

個人利用を目的としています。Twitch API の利用規約に従って使用してください。VOD ダウンロード機能は、自身のチャンネルのバックアップ目的での使用を推奨します。
