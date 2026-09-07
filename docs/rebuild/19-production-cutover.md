# 新Botへの本番切替

2026-09-07、ユーザーの明示的な切替依頼に基づき、新版を本番の記録担当にした。
設計全体の完成宣言ではなく、以下の範囲での運用開始を記録する。

## 起動と引継ぎ

- 同じアプリの単一コンテナ、ポート8501、既存のdata/downloadsマウントを維持。
- `python -m twitchbot.serve --integrated --configuration <設定ファイル> --bind 0.0.0.0:8501`で起動。
  `PYTHONPATH`にはアプリルートとsrcを含める。`/`は新ライブ画面へ移動する。
- 旧IRC・viewer・自動投稿workerを停止。新しい配信者ログイン認証を実機へ移した。
  PCの設定用プロセスは停止し、設定・grantファイルは退避名に変更して通常のresumeを防止。
- SQLiteとバックアップ作業領域はdata配下の専用領域。OAuth grantはその外の非公開領域に分離。
- 配信終了後と毎日04:00 JSTのNAS自動保存を有効化。送信用Botアカウントは未設定。
- 既存イメージを基にローカルビルドし、実際のComposeプロジェクト名を維持した。
  registryへのpush、git commit/pushは行っていない。リポジトリの既定composeは旧起動設定のため、
  本番の明示的な起動設定を無視して上書きしない。

| 対象 | 結果 |
|---|---|
| 配信 | 37件 |
| 同接サンプル | 2,297件。タイムゾーン不明の618件は保留 |
| 旧人物レコード | 145件。新コミュニティの継続記録とは区別 |
| VODメタデータ | 37件。録画本体は移動しない |
| 配信セット | 8件。Twitchで全カテゴリーを照合し、タイトル・カテゴリーID/名・タグ・告知用タグを保存後に照合 |

旧JSON/JSONLの原本と停止後の退避を保持した。旧チャット本文・アクティビティ等の保留は[18](18-integrated-host-and-migration-rehearsal.md)のとおり。
新配信履歴から`/analytics`、コミュニティから`/legacy/viewers`へ進み、切替前の記録を参照できる。
切替後のフォロー同期・イベントは新コミュニティへ記録する。

`/api/stream/status`は新レコーダーのキャッシュを既存の応答形式に変換する。
GETでTwitchを呼ばず、停止時は配信情報を消し、通信劣化時は最後の実観測を保持する。
VOD同期・検索は新認証の同じHelixクライアントを使い、回転したgrantと通信制限を共有する。
旧設定JSONへ新トークンをコピーしない。

## 検証と修正

- Windows: 437 passed / 15 skipped。実機arm64: 452 passed。
- 実際のGunicorn起動で、既存API、旧履歴/VOD、新画面、入力エラーのHTTP 400を確認。
- 初回切替後の状態取得タイムアウトでは旧版へ復旧。Operationsの設定ロックを保持したままHelix状態を読む箇所で、
  通信処理の実行可否確認とロック取得順が逆になる問題を修正した。
  回帰テストを追加し、別保存先の実認証ランタイムで確認してから再切替。
- 実認証で配信外を取得、フォロワー同期、EventSubの11種類の購読を確認。
  再検証時の状態GETを12回繰り返し、最大応答時間は約0.04秒。
- NAS上の2件を検証済みとして確認。NASから別領域へ読み戻し、8セットを含む停止済み復元候補を作成。
- 配信外で常に配信切替の警告を出していたJavaScript条件を修正。構文確認と本番ブラウザーで再確認。
- PCのブラウザーから本番の新ライブ画面と8セットの一覧を確認。

配信中の長時間収集、実イベントの自然発生、Twitchマーカーの実作成、トークンの期限到来による自動更新は今後の実運用での確認事項。
検証のためのチャット投稿、配信タイトル変更、予想開始、VOD新規ダウンロードは実行していない。

## 復旧と関連変更

実機の非公開リリース領域に旧Compose、旧データ退避、切替記録、復旧スクリプトを保存した。
復旧時は先に新版を停止し、writer終了後に旧構成を再作成する。
新版のDBと最新の回転済みgrantを保持し、古いrefresh tokenへ戻さない。
旧JSONへ新版の記録を推測でマージしない。NAS復元は停止済みの別候補へ行う。

- 統合: [routes/application.py](../../routes/application.py)、[services/v2_host.py](../../services/v2_host.py)、[serve.py](../../src/twitchbot/serve.py)
- 互換API/VOD: [dashboard.py](../../routes/dashboard.py)、[vod.py](../../routes/vod.py)、[twitch_api.py](../../services/twitch_api.py)、[download.py](../../services/download.py)
- 状態取得: [operations.py](../../src/twitchbot/application/operations.py)
- 回帰確認: [test_v2_primary_host.py](../../tests/test_v2_primary_host.py)、[test_v2_controls.py](../../tests/test_v2_controls.py)
