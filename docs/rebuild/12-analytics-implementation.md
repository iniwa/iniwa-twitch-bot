# 12. 同接と履歴の初期実装

更新日: 2026-09-06

[11の工程A・B](11-implementation-contract-and-delivery.md)について、候補DBを使う保存・集計・読み取り画面を追加した。
ソースと架空データの隔離検証の到達点であり、Twitchからの実収集や現行アプリへの切替は未実施。

## 実装した範囲

| 領域 | ソースと動作 |
|---|---|
| 同接集計 | [application/analytics.py](../../src/twitchbot/application/analytics.py)。最大30秒の持ち越しによる時間加重平均、元の観測値による最大値・最初のピーク時刻、記録率。0人と欠測を区別 |
| 保存 | [migration 3](../../src/twitchbot/adapters/persistence/migrations.py)。収集実行、同接観測、欠測、データ版・終了時刻の確かさを追加。migration 1・2は変更しない |
| 記録操作 | [AnalyticsRepository](../../src/twitchbot/adapters/persistence/analytics.py)。実行開始・停止、観測の追記、欠測の開始・終了。同じ入力の再実行は無変更、競合は拒否。観測とデータ版は同じトランザクション |
| 履歴読み取り | 同ファイルの`HistoryReader`。SQLiteの読み取り専用接続・単一トランザクションで数値とグラフを取得。比較の2配信も同じスナップショット・集計時点 |
| API・画面 | [web/history.py](../../src/twitchbot/web/history.py)、[テンプレート](../../src/twitchbot/web/templates/v2/history.html)、[CSS](../../src/twitchbot/web/static/v2/history.css)、[JS](../../src/twitchbot/web/static/v2/history.js)。一覧、期間指定の詳細、配信全体または共通経過時間の比較。日時はJST |

既存の`stream_samples`や旧平均・最大は書き換えない。旧集計は詳細内の別欄に表示する。
終了時刻の確かさは初期値`unknown`。不明な記録率や共通経過時間を、確定した値として表示しない。
推定終了時刻の記録率は参考値として明記する。

## 接続と利用境界

- 独立したv2 factoryの`Container(history_reader=HistoryReader(candidate_database))`へ明示的に注入する。
- migrationは呼び出し側の明示操作。factory・画面アクセスはDBを作成・移行せず、Twitchへ問い合わせない。
- 読み取り元が未設定ならAPIは503、画面は保存先未接続の説明を返す。
- 現行のルート登録・収集ワーカー・公開ステータス・scheduler・配備設定は変更していない。
- 再起動時は以前の実行を閉じて別の実行IDを開始する。停止時刻が不明な場合も値の持ち越しは最大30秒。
  実際の異常終了検出と収集再開の自動処理は工程Gで接続する。

| URL | パラメーター |
|---|---|
| `GET /api/v2/streams` | `limit`は初期50・最大200、`cursor`は返却された`next_cursor_token` |
| `GET /api/v2/streams/<id>/analytics` | `start`・`end`はUTC RFC3339、`points`は32〜5000・初期1200 |
| `GET /api/v2/stream-comparisons` | `id`を2つ、`scope=full`または`common`。同じIDの重複は拒否 |
| `/v2/history`・`/v2/history/<id>`・`/v2/history/compare` | 上記データの読み取り画面。比較は一覧で2件を選択 |

集計応答は`method`、`data_revision`、`as_of`、範囲、人数×秒、観測できた秒数、平均・最大・ピーク、
指標別品質、終了時刻の確かさ、旧集計、グラフの方式・点数予算を含む。
データ版は配信メタデータの版と同接データの版の組。APIは`Cache-Control: no-store`。

グラフは予算内なら元の区間を階段状に描画する。超過時は連続区間ごとに先頭・末尾・最小・最大を保持し、
区間の最小〜最大の幅として表示する。欠測をまたぐ集約はしない。
欠測の数だけで予算を超える場合は、集計値を残して期間を絞る案内を出す。
読み取りは各配信・各テーブル最大10万行で制限し、超過を空データや0人に置き換えない。
観測値は指定範囲と開始前30秒だけを読む。長期一覧の事前集計・実機の負荷調整は未実施。

## 検証

- [test_v2_analytics.py](../../tests/test_v2_analytics.py)：設計の数値例、通信断、再起動、JST日付またぎ、最初の観測前、0人、端点、旧集計との区別。
- 同ファイル：migration 2からの前進更新、再実行・競合・ロールバック、版の一貫性、書き込み中の読み取りスナップショット、参照削除、読み取り専用接続、未設定時の副作用抑制、API検証・HTMLエスケープ、グラフの点数予算と欠測・ピーク。
- 既存テストのmigration 2検証はmigration 2を明示して維持。未来のschema版の検出は最新版に追従。
- 一時的な検証環境で`python -m pytest tests/ -q`を実施。検証環境の追加はプロジェクトの依存定義を変更しない。
  最終結果は271件成功、2件スキップ。スキップはOSが架空のディレクトリ／壊れたシンボリックリンク作成を許可しない既存テスト。
- 架空データをFlask test clientで描画し、Edgeのheadlessブラウザーで1440・1024・736・360px幅を確認。ページ全体の横はみ出し、JavaScript例外、グラフ生成、JST期間入力を検証。

## 後続工程

以下はA・B実装時点の後続計画。C・D・Eの初期実装の到達点は[13](13-community-controls-and-backups.md)を参照する。

工程Cではイベント・チャット・フォロー履歴と人物の配信参加記録を追加する。
配信外の出来事、重複受信、部分的な同期、再フォロー、本文だけの手動削除を先に隔離検証する。

今回の同接記録は配信単位に限定する。取得元・受信IDの詳細、配信外の指標別欠測、終了確認の根拠と不確実な時間範囲、
タイトル・カテゴリーの変更履歴、持続的な集計キャッシュは後続の追加対象。
Twitch実接続、NAS保存・復元、実データ移行、arm64での負荷検証、本番切替は未実施。
