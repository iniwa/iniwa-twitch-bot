# コードベース改善チェックリスト

> Gemini 産コードを Claude Code でレビューした結果に基づく改善案。
> チェックを付けた項目から順次実装していく。

---

## 1. セキュリティ (Security)

### 1-1. 認証・認可

- [ ] **全 POST エンドポイントに認証チェックを追加** — 現在すべてのルート (`routes/*.py`) に認証がなく、誰でも設定変更・bot 制御が可能
- [ ] **CSRF トークン保護の導入** — Flask-WTF 等で POST リクエストを保護する

*これはそれぞれの環境のローカル下で動作させるBOTのため、認証は不要かと思います。*  
*また、Cloudflared Tunnel時点で認証を挟んでいるため、セキュリティリスクも軽微と考えています。*  

### 1-2. XSS 対策

- [x] **dashboard.js: `updateMonitor()` 内で `escHtml()` を全箇所に適用** — `rule.message` 等がエスケープされておらず XSS 可能
- [x] **analytics.js: ゲーム名等の `innerHTML +=` をエスケープ処理** — API レスポンスがそのまま HTML に挿入される
- [x] **テンプレート内 JS 関数呼び出しを `data-*` 属性方式に統一** — `onclick="func('{{ value }}')"` パターンの手動エスケープ (`replace("'", "\\'")`) は不完全。対象: `_viewer_card.html`, `_preset_card.html`, `dashboard.html`, `analytics_list.html`

### 1-3. パストラバーサル

- [x] **analytics.py / vod.py: `stream_id` のバリデーション追加** — `stream_id` がファイルパスに直接使用されており、`../` を含む入力でディレクトリトラバーサル可能

### 1-4. 入力バリデーション

- [x] **routes 全体: フォーム入力の型チェック・長さチェックを追加** — `int()` 変換時の `ValueError` ハンドリング、文字列長さ制限が未実装。対象: `rules.py`, `predictions.py`, `presets.py`, `viewers.py`, `settings.py`
- [x] **settings.py: API トークンの形式バリデーション** — `client_id`, `access_token`, `broadcaster_token` の形式チェックなし

---

## 2. 安定性・エラーハンドリング (Reliability)

### 2-1. アプリケーション起動

- [x] **app.py: 本番環境向け WSGI サーバー (gunicorn) の導入** — Flask 開発サーバーを直接使用しており、パフォーマンス・安定性に問題
- [x] **app.py: 起動時のエラーハンドリング追加** — モジュール読み込み失敗時にプロセスが静かに終了する

### 2-2. エラーハンドリング統一

- [x] **routes 全体: エラーハンドリングパターンを統一** — 同じ処理 (`int()` 変換等) でも try-except がある箇所とない箇所が混在。特に `predictions.py`, `presets.py`
- [x] **twitch_api.py: `update_channel_info()` のレスポンスステータスコード確認** — PATCH 後の成功チェックがない
- [x] **predictions.py: `get_current_prediction()` の例外黙殺を修正** — リトライなし・ログなしで失敗が隠れる

### 2-3. スレッド安全性

- [x] **workers.py: `current_minute_stats` のディープコピー問題を修正** — `raids` リストが浅いコピーで共有参照になっており、複数スレッドの `.append()` で競合
- [x] **irc.py / workers.py: ロック取得順序を統一** — `stats_lock` と `file_lock` の取得順序が不一致でデッドロックの可能性
- [x] **download.py: `load_stream_index()` → `save_stream_index()` 間の競合対策** — アトミックでない読み書きで状態不整合のリスク

### 2-4. グレースフルシャットダウン

- [x] **workers.py: daemon スレッドのグレースフルシャットダウン実装** — 3 スレッドすべて `daemon=True` で、シャットダウン時にデータ損失の可能性

---

## 3. パフォーマンス (Performance)

### 3-1. I/O 最適化

- [x] **irc.py: 毎メッセージごとの `load_viewers()` / `save_viewers()` を改善** — チャットメッセージ毎にディスク I/O が発生。インメモリキャッシュ + 定期フラッシュに変更すべき
- [x] **twitch_api.py: `force_update_followers()` のファイルロック長時間保持を改善** — ロック中にファイル I/O 完了まで他処理がブロックされる

### 3-2. フロントエンド

- [x] **analytics.js: `innerHTML +=` ループを `DocumentFragment` に変更** — 毎回リフロー・リペイントが発生しパフォーマンス低下
- [x] **analytics.js: 重複関数 (`renderSimpleList` / `renderMonthlyList` / `renderWeeklyList`) を統一** — ほぼ同一のコードが 3 箇所に存在
- [x] **dashboard.js: ポーリングを `setTimeout` チェーンに変更** — `setInterval` では前回 fetch 未完了時にリクエストが溜まる

### 3-3. API 効率

- [x] **twitch_api.py: `get_chatters()` にページネーション上限を設定** — `while True` で無制限。大規模チャンネルで問題
- [x] **twitch_api.py: API タイムアウト値を統一** — 5〜15 秒でバラバラ。定数として一元管理すべき

---

## 4. インフラ・ビルド (Infrastructure)

### 4-1. Docker

- [x] **Dockerfile: Python バージョンを 3.12-slim に更新** — Python 3.9 はセキュリティ更新が終了済み
- [x] **Dockerfile: HEALTHCHECK 命令を追加** — コンテナの自動復旧が機能しない
- [x] **Dockerfile: ユーザー ID を ARG で可変化** — `1000:1000` のハードコーディングを解消

### 4-2. 依存関係

- [x] **requirements.txt: バージョンピンニングを実装** — `flask`, `requests`, `yt-dlp` のバージョン未固定。予期しない破壊的変更のリスク

### 4-3. ビルド設定

- [x] **.dockerignore: `.git`, `.github` 等の除外を追加** — 不要ファイルがイメージに含まれている
- [x] **.gitignore: `.env` ファイルの除外を追加** — 機密情報の誤コミット防止

---

## 5. コード品質 (Code Quality)

### 5-1. コード重複

- [x] **dashboard.py: 「無視対象ユーザー」フィルタリングを共通関数に抽出** — 3 箇所 (`get_history_api_data`, `get_active_viewers_data`, `index`) で同一ロジックが重複
- [x] **filters.py: datetime パース処理の統一** — `to_datetime()` と `format_date()` で重複、dashboard.py の `_timestamp_to_date()` とも重複

### 5-2. 設定のハードコード

- [x] **app.py: ポート番号を環境変数から取得** — `8501` がハードコード
- [x] **config.py: 相対パスを絶対パスまたは環境変数に変更** — `'data/config.json'` 等が作業ディレクトリ依存
- [x] **download.py: スリープ間隔 (`time.sleep(300)`) を設定化** — 5 分固定
- [x] **workers.py: オフライン猶予・ログフラッシュ間隔を設定化** — マジックナンバーが散在

### 5-3. ログ

- [x] **全体: ログメッセージから絵文字を除去、構造化ログに移行** — Gemini 由来の絵文字付きログ (`✅`, `⚠️`, `ℹ️`) がログ解析を困難にしている

### 5-4. フロントエンド構造

- [x] **JS: グローバル関数をモジュール化** — `common.js`, `dashboard.js` でグローバル名前空間を汚染
- [x] **CSS: カラー変数を統一** — `.btn-*` クラスの色定義が分散。CSS カスタムプロパティで一元管理すべき

---

## 6. UX・アクセシビリティ (UX / A11y)

- [x] **base.html: ライブバッジに `aria-live="polite"` を追加**
- [x] **analytics_list.html: クリック可能な `<td>` を `<a>` タグに変更** — キーボードナビゲーション非対応
- [x] **prediction_card.html: プログレスバーにテキストラベルを追加** — 色のみで情報を表現しており色覚異常対応不足
- [x] **analytics.css: カレンダーのモバイル対応** — `min-width: 800px` 固定で小画面で横スクロール発生

---

## 優先度ガイド

| 優先度 | カテゴリ | 目安 |
|--------|----------|------|
| **P0** | セキュリティ 1-2, 1-3 | XSS・パストラバーサルは即対応 |
| **P1** | セキュリティ 1-4, 安定性 2-3 | 入力バリデーション・スレッド安全性 |
| **P2** | インフラ 4-1, 4-2 | Python更新・バージョンピン |
| **P3** | パフォーマンス 3-1, 3-2 | I/O最適化・フロントエンド改善 |
| **P4** | コード品質 5-1〜5-4, UX 6 | リファクタリング・改善 |

> **注**: 認証 (1-1) はアーキテクチャレベルの大きな変更。ローカルネットワーク専用なら優先度を下げてもよいが、外部公開 (Cloudflare Tunnel) する場合は P0。
