# 16. 新版の起動・移行候補・本番切替の前提

更新日: 2026-09-07

本書は切替の準備資料。記載したコマンドを本番で実行済みとは扱わない。
その後の明示的な切替依頼による実施結果は[19](19-production-cutover.md)を参照する。
旧画面・VODと新版の経路を同じFlaskへ組み込む構築関数は[18](18-integrated-host-and-migration-rehearsal.md)で追加した。
新版だけを動かす最終構成の公開ステータス・VOD認証・収集担当の統合はまだ残る。
この状態での本番切替は行わない。[15](15-runtime-and-connections.md)の未完了項目を先に解消する。

## 明示的な検証ホスト

`bootstrap.from_file`は明示された設定JSONと、その中で指定した資格情報ファイルだけを読む。
親ディレクトリを探索して認証を補完しない。設定例のパスは説明用で、実機の値ではない。

```json
{
  "database_path": "/path/to/isolated/candidate.sqlite3",
  "staging_root": "/path/to/isolated/backups",
  "channel_id": "123456",
  "credentials_file": "/path/to/private/broadcaster.json",
  "bot_credentials_file": "/path/to/private/bot.json",
  "nas_root": "/path/to/mounted-nas/dedicated-backups",
  "nas_source": "<verified-mount-source>"
}
```

`bot_credentials_file`は任意。未指定なら自動投稿とコマンドを送信できないが、定義保存・プレビューは利用できる。
NASの2項目を省略すればローカルバックアップだけを使う。
資格情報ファイルのキーは `client_id`、`user_id`、`access_token` の3つ。
資格情報をrepository、DB、バックアップ、会話へ貼り付けない。
ユーザーが希望した通常の接続方式はTwitchログインとする。上記は旧形式互換の設定例。
ログイン方式では `credentials_file` の代わりに `oauth_file` を使う。[17](17-twitch-authorization.md)に初回設定を示す。

Linuxでの起動例。選択した既存の待受境界を使い、他のプロセスが所有する待受を奪わない。

```sh
PYTHONPATH=src python -m twitchbot.serve \
  --configuration /path/to/private/runtime.json \
  --bind unix:/path/to/isolated/runtime.sock
```

起動後は `/v2/settings` から記録を明示的に有効化する。
未設定DBでは記録・自動化が停止状態で始まる。Botの送信はさらに自動化画面の機能と個別定義を有効にした場合だけ動く。
設定ファイルを用意しただけで旧schedulerの停止やデータ移行は行わない。

## 移行候補の作成

実データの移行元を固定したスナップショットとして別途準備し、資格情報を含まないことを確認する。
旧ファイルを直接編集して資格情報を消したり、稼働中の保存先へ新版を上書きしたりしない。
候補インポーターは忠実に表現できない形式を保留として報告する。件数が合うだけで切替可とはしない。

[prepare_v2_candidate.py](../../tools/prepare_v2_candidate.py)は明示した移行元とdownloadsのメタデータを調べ、
新しい候補DBにだけ保存し、検証済みの件数・集計レポートを出力する。動画の本文はコピーしない。

```sh
PYTHONPATH=src python tools/prepare_v2_candidate.py \
  --source /path/to/sanitized-legacy-snapshot \
  --downloads /path/to/downloads-reference \
  --candidate /path/to/isolated/import-candidate.sqlite3 \
  --reference cutover-candidate-01
```

既存の候補DBは上書きしない。資格情報を検出した場合は保留し、エラーに実値を含めない。
レポートの保留・拒否項目、時刻の解釈、同接の計算方式、人物履歴、セット・ルールの引継ぎを照合する。
失敗した候補は自動的に起動しない。

## 切替前にそろえる成果物

1. 配信者とBotそれぞれの認証主体・必要権限の検証結果。実Twitchの取得・イベント・操作の確認。
2. 旧API・VODの経路を維持した起動構成と、旧・新の送信担当が重複しない停止順序。
3. 対象スナップショット、移行・比較レポート、保留項目の扱い。
4. 現行データと現行イメージの作業前退避、新版候補、NASから復元できた候補。
5. 同じport 8501・data/downloads境界で切り替える具体的な配備差分と、切替可能な時間帯。

揃った後に、実際に変更する配備と復旧手順を示して本番切替を確認する。
ソース実装・隔離テストのたびに切替許可を求める手順にはしない。

2026-09-07時点では、新規アプリの配信者認可、配信状態・フォロワー取得、EventSub購読、
取得済みDBの実NAS保存・読み戻し・停止済み候補への復元まで確認した。
実認証の再起動復旧も確認済み。Botは将来の送信用として未設定。
旧API・VODの実行担当の統合、旧データの保留項目の移行、実トークンの自動更新・配信中の継続収集は残る。
旧データの20ファイルから停止済み候補DBを作り、配信37件・人物145件等を照合した詳細は[18](18-integrated-host-and-migration-rehearsal.md)に記録する。
単発の検証結果を常用設定や本番切替の完了とは扱わない。詳細は[17](17-twitch-authorization.md)を参照する。

## 切替と復旧の順序

切替時は旧送信・収集を停止し、停止後のスナップショットで候補を再照合してから、新版の単一writerを開始する。
公開ステータスの互換、配信情報の鮮度、同接・イベント記録、NASの保存結果を確認する。
初回は自動投稿・コマンド・予想の新規開始を無効にした状態を維持する。

問題があれば先に新版の実行許可を閉じ、全ワーカー終了を確認してから旧アプリと旧データへ戻す。
新版で受信したデータは別に保全し、旧データへ推測でマージしない。
すでにTwitchへ届いた操作を、DBのロールバックで取り消せるとは扱わない。
終了が確認できないプロセスのロックを奪取して、別writerを強制起動しない。
