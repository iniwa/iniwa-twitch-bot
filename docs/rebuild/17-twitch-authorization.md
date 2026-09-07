# 17. Twitchログインと認証の更新

更新日: 2026-09-07

## 採用した操作

ユーザーの希望に合わせ、新版は画面からTwitchにログインする方式とした。
先に案内したCLIによるトークンの手動取得は通常の利用手順から外す。

1. 新版の「設定」→「Twitchログイン・再認証」を開く。
2. 配信者アカウントの「Twitchログインを開始」を押す。
3. 表示された確認コードを確認し、「Twitchを開いてログイン」を押す。
4. Twitch公式画面で配信者本人のアカウントと要求権限を確認して許可する。
5. アプリに戻ると認証主体・権限を自動確認し、認証情報を保存する。
6. 記録を始めるときは設定画面から明示的に開始する。

Botは将来自動投稿やコマンドを使うときに、別のアカウントとして接続する。
予想の管理権限は配信者側のチェックボックスで追加できる。
ログインだけで停止中の記録や自動投稿は開始しない。
パスワードの入力先はTwitch公式画面。アプリ画面へトークンを貼り付ける操作は不要。

## 方式と接続の境界

TwitchのDevice Code Grantを使う。Pi上のアプリで認可要求を発行し、PCのブラウザーでTwitchにログインする。
コールバック用の公開URLや新しい待受ポートは追加しない。
Twitch Developersのアプリ登録情報と、利用者がログインして得るアクセストークンは別のもの。
参照: [Device Code Grant](https://dev.twitch.tv/docs/authentication/getting-tokens-oauth/#device-code-grant-flow)。

[OAuthアダプター](../../src/twitchbot/adapters/oauth.py)が公式エンドポイントにだけ接続し、
Twitchから返されたログイン先のscheme・host・pathを検証する。
状態APIへ返すのは確認コードと公式ログイン先、処理状態だけ。device code・access token・refresh token・Secretは返さない。
GETとログイン開始POSTはOAuth通信を直接実行せず、専用ワーカーが処理する。
認可待ち・期限切れ・拒否・取得制限・キャンセルを扱う。

[認証ワーカー](../../src/twitchbot/application/login.py)は、設定されたClient ID・ユーザーIDと、
Twitchの検証結果が一致した場合だけ認証を採用する。必要権限が不足する場合も採用しない。
間違ったアカウントで新しくログインしても、以前に確認できた認証を置き換えない。
トークン変更時はHelixの検証キャッシュを破棄し、EventSubが自身のワーカーで接続と購読を作り直す。

## 自動更新と再起動

記録の稼働中は、有効期限または認証エラーに応じて自動更新する。
更新は単一ワーカーと専用保存領域のOSロックで直列化し、更新トークンを並行利用しない。
更新開始の記録を先に永続化し、新しいトークン対は検証前の候補として保存する。
検証に成功してから稼働用に採用する。

更新通信の結果が不明な場合や、更新途中で再起動した場合は、消費済みかもしれない古い更新トークンを自動再送しない。
新しい対を保存済みで検証だけ未完了なら、その検証を再開する。
自動更新できない場合は画面から再ログインする。
記録を停止した間は自動更新も休止するが、明示的なログインは利用できる。
参照: [トークン更新](https://dev.twitch.tv/docs/authentication/refresh-tokens/)、
[認証検証](https://dev.twitch.tv/docs/authentication/validate-tokens/)。

## 初回だけ必要なホスト設定

今回の実接続確認では、[PC内の初期設定ツール](../../tools/serve_v2_login_setup.py)も用意した。
配信者のユーザーIDは指定済みの対象を使い、新版専用に登録したClient IDをPC内の画面で入力する。
初期選択は `public`。Secretは不要で、保存後の明示的なDevice Codeログインで登録情報と本人を確認する。
`confidential` を選ぶ場合は同じアプリのSecretも入力し、保存前にTwitchでアプリ登録情報を確認する。
旧Client IDやBotアカウントの認証は自動で引き継がない。Bot認証は必要になった時にホスト設定へ追加する。
独立した検証領域に保存してからログイン画面へ進み、記録開始は別の明示操作とする。
待受はloopbackのみ、異なるHost・Originや外部アドレスからの操作を拒否する。
元のBotの認証ファイルや稼働データへは書き戻さない。このツールは本番入口の置換ではない。
Origin拒否・入力不備・Twitchの拒否・通信失敗・ローカル保存失敗を固定文言で区別する。
状態APIには設定済みフラグと安全なエラーコードだけを返し、入力値や上流エラー本文を含めない。
入力画面・再起動のテスト11件と認証テスト16件はWindowsで成功した。

PC内の検証ホストは、初回設定で明示した作業領域を指定して再開できる。
既存プロセスの終了を確認してから実行する。別の待受番号を指定しても同じ作業領域の二重起動は拒否する。

```sh
PYTHONPATH=src python tools/serve_v2_login_setup.py \
  --resume --work-root /path/to/isolated/setup-area --port 53826
```

指定領域の `private/runtime.json` だけを入口に設定を読み、初期設定の入力経路は再開しない。
資格情報を上書きせず、保存された記録設定に従う。記録停止中の再開ではTwitch通信を開始しない。
認証保存・停止設定の維持、二重起動拒否、失敗時の所有権解放、loopback制約を合成データで確認した。
実認証を使ったプロセス再起動でも、保存済み認証と記録停止設定の維持を確認した（2026-09-07）。
再開後に短時間記録を有効化し、再ログインなしでHelixの検証・配信状態取得・フォロワー同期・
11種類のEventSub購読が復旧した。検証後は再び記録を停止した。

新規登録の手順は [Twitch公式の登録ガイド](https://dev.twitch.tv/docs/authentication/register-app/) を参照する。
新版用の名前とカテゴリーを設定し、Client Typeは `Public` を選ぶ。
登録欄のRedirect URLには `http://localhost:3000` を指定できるが、Device Code方式ではリダイレクトは使わない。
Public方式はSecretなしで更新できる。30日間使われなかった更新トークンは期限切れとなり、再ログインが必要。
参照: [Device Code方式](https://dev.twitch.tv/docs/authentication/getting-tokens-oauth/#device-code-grant-flow)。

ホスト管理者がアプリ登録情報と保存先を一度構成する。
既存のTwitch Developersアプリを使う場合は、Client Typeを先に確認する。
`confidential`では自動更新に既存のClient Secretが必要。`public`ではSecretを指定しない。
既存アプリのClient TypeやSecretを、この実装のために勝手に変更・再発行しない。

新版の明示的なruntime JSONは、`credentials_file`の代わりに `oauth_file` を指定する。
Botを含む旧形式の資格情報ファイル指定とOAuth指定を混在させない。

```json
{
  "database_path": "/path/to/isolated/candidate.sqlite3",
  "staging_root": "/path/to/isolated/backups",
  "channel_id": "123456",
  "oauth_file": "/path/to/private/oauth-application.json"
}
```

`oauth-application.json` の説明用構造。値は実環境のものではない。

```json
{
  "private_root": "/path/to/private/grants",
  "accounts": {
    "broadcaster": {
      "client_id": "APPCLIENTID",
      "user_id": "123456",
      "client_type": "confidential",
      "client_secret": "SETLOCALLY"
    }
  }
}
```

配信者のuser_idはchannel_idと一致させる。Botが必要になったら `accounts.bot` にBotの同形式設定を追加する。
保存先は事前に用意した専用ローカルディレクトリ。Linuxではディレクトリ0700・資格情報ファイル0600を使う。
Windowsでは実行ユーザーだけがアクセスできるディレクトリを用意する。
資格情報はSQLiteや、このアプリが作成するNASバックアップへ含めない。
既存の手動トークン構成は互換のため残すが、OAuth未設定時に認証を推測して読み込まない。

## 検証範囲

合成データのテストでは、保存・再起動・誤アカウント・権限不足・更新失敗・更新候補の検証再開・
保存失敗時の送信防止・認可の期限・頻度制限・キャンセルを確認した。
HTTP状態読み取りがOAuth通信を待たないこと、未認証の仮値をTwitchへ送らないことも確認した。

[ブラウザー検証](../../tools/verify_v2_login.cjs)は架空OAuth応答と実ワーカーを使う。
Piの全体テストは414件成功、スキップなし。Windowsの全体テスト395件成功・15件スキップ後に、
追加4条件を含む認証テスト16件を再検証して成功した。Piではこれらも全体テストに含む。
Piのブラウザーでは配信者・Botの接続と各1回の自動更新、4表示幅、状態APIにトークンがないことを確認。
ページ内のJavaScriptエラーなし。ログインによる記録開始・チャット送信なしも確認した。
これらの合成データによる検証は、実Twitchへのログインや本番切替の確認とは分ける。
旧アプリの配信者用トークンは以前の読み取り検証で `authorization_required` だったが、原因は断定していない。

### 新規アプリでの実接続確認（2026-09-07）

ユーザーが新版用アプリの設定とTwitchログインを完了し、独立したPC内の検証環境で
配信者アカウントの `connected`、認証ワーカーの `ready`、認証保存ファイルの存在を確認した。
資格情報の値を確認結果やドキュメントへ記載しない。

短時間だけ記録を有効にし、以下を確認してから停止状態へ戻した。

- Helix接続が `ready` となり、配信状態・チャンネル情報を取得。配信状態はオフラインだった。
- フォロワー一覧の同期が `complete` となった。
- EventSubが `connected` となり、配信開始・終了、フォロー、チャット、Cheers、Raid、
  サブスク・更新メッセージ・ギフト、ポイント引き換え、予想終了の11種類を購読した。
- マーカー・配信セットの必要権限を確認。作成や配信情報の変更は実行していない。
- 記録停止後は記録・イベントワーカーが `paused`、ログイン状態は `connected` を維持した。

実イベントの到着・保存、配信中の同接サンプル、実トークンの自動更新は
この短時間の接続確認では未検証。新版の常用NAS保存先設定と本番切替も別工程として残る。

同日に、この検証で取得したフォロワーデータを含むDBの整合したバックアップを作成した。
DBスナップショットとmanifestの2ファイルだけをPiの専用領域へ転送し、認証ファイルは転送しなかった。
実CIFS上の新しい専用領域で `MountedNasTransfer` による保存・checksum確認を行い、
空の別ローカル領域へNASから取得したコピーから、停止済みの復元候補を作成した。
結果は `nas_verified` / `candidate_verified`、復元前後の人物件数は一致した。
これは単発の実データ検証であり、常用ランタイムへのNAS設定や自動バックアップの有効化ではない。
