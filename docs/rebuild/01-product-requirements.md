# 01. プロダクト要件

## 1. プロダクトビジョン

配信準備、配信中の判断、視聴者対応、配信後の振り返りを、一人の配信者が
迷わず繰り返せるローカル運用コンソールにする。

v2 の価値は機能数ではなく、次の 3 点で測る。

- 今やるべきことと Bot/Twitch の状態が一目で分かる。
- Twitch やネットワークが不安定でも、何が起きていて何を再実行できるか分かる。
- 日々の運用を改善しながらも、既存履歴と VOD を安全に引き継げる。

## 2. 背景と現状課題

現行版は、視聴者、プリセット、自動チャット、予想、イベント、ログ、設定を
一つのダッシュボードと多数のモーダルへ集約している。機能は揃っているが、
配信準備・配信中・配信後で優先順位が変わっても、同じ密度で表示される。

コード面では、ルートから Twitch API を同期呼び出す箇所、import 時に開始する
ワーカー、process-local lock に依存した JSON read-modify-write、接続・ドメイン・表示の
責務混在がある。現行テストは重要な snapshot と VOD 境界を守っている一方、
外部障害、token refresh、worker lifecycle、主要 UI フローのカバレッジが薄い。

このため v2 は、CSS の張り替えではなく、ユーザータスク、状態モデル、境界、保存、
検証を同時に再設計する。

## 3. 想定利用者

### P1: 配信者兼オペレーター（主利用者）

- 単一チャンネルを本人が運用する。
- 配信開始前にタイトル・カテゴリー・ルールを準備する。
- 配信中は Bot の健全性、視聴者、イベント、予想を素早く確認・操作する。
- 誤操作を避けたいが、頻繁な確認ダイアログで作業を止めたくない。

### P2: モデレーター（副利用者）

- 視聴者情報を確認し、メモ、shoutout、フォロワー同期を行う。
- Twitch の認可範囲外や Bot 停止中で操作不能な理由を理解する必要がある。

### P3: 配信後の分析者（同一人物の別モード）

- 配信を検索・比較し、同接、コメント、イベント、視聴者参加を振り返る。
- VOD の同期、ダウンロード、失敗再試行、削除を安全に行う。

## 4. Jobs to be Done

| 状況 | やりたいこと | 成功状態 |
|---|---|---|
| 配信前 | 保存済みセットを適用して接続状態を確認する | タイトル、カテゴリー、Bot、権限が準備済み |
| 配信開始直後 | Twitch の実ライブ状態を確認する | live snapshot と最終確認時刻が明確 |
| 配信中 | 異常・イベント・必要操作だけを見る | 重要な状態変化を見逃さず操作できる |
| レイド受信時 | 対象を確認して shoutout する | 対象と結果が履歴に残る |
| 予想実施時 | プリセットから開始し、結果を確定する | 二重開始や誤確定を防げる |
| 配信終了後 | サマリーと VOD 状態を確認する | 終了処理、履歴、必要な download が追跡可能 |
| 障害時 | どこで止まったか判断して再試行する | 原因、影響、次の操作が画面に出る |

## 5. UX 原則

1. **状態が先、操作が次** — Twitch live、Bot、EventSub、token、保存、VOD job を
   一つの曖昧な「稼働中」にまとめない。
2. **文脈内操作** — 予想はライブ画面、ルール編集は自動化画面など、操作を目的の
   近くに置く。
3. **安全な既定値** — 自動 VOD は無効。破壊操作は対象と影響を明記する。
4. **障害を隠さない** — stale data、再接続中、権限不足、rate limit を区別する。
5. **段階的な詳細** — 日常状態は簡潔に、詳細ログや高度な設定は必要時に展開する。
6. **同じ概念は同じ言葉** — `Bot`, `Twitch 接続`, `配信`, `アーカイブ` を混同しない。

## 6. 機能スコープ

### Must: v2 初回切替までに必要

- Twitch OAuth 接続状態、scope、token 更新状態の表示。
- Bot actor と broadcaster actor の identity、scope、認可状態を分けた表示。
- Bot 有効/無効、EventSub/互換 IRC 接続、再接続状態の表示と制御。
- 実際の Twitch live snapshot、タイトル、カテゴリー、開始時刻、視聴者数。
- 配信プリセットの作成、編集、適用。
- 現在設定または preset から X 告知文を作り、投稿 intent を利用者操作で開く。
- 自動チャットルールの作成、編集、並べ替え、有効化、実行状態。
- チャンネルポイント予想の作成、preset、開始、確定、キャンセル。
- 現在セッション視聴者、累積視聴者、メモ、shoutout、フォロワー同期。
- サブスク、ギフト、レイド、Bits、フォロー、チャットのイベント表示と履歴。
- 配信履歴、時系列統計、検索、比較の基礎。
- Twitch VOD 同期、個別/一括 download、進捗、取消、再試行、削除。
- `GET /api/stream/status` の完全互換。
- 現行 JSON/JSONL の検証付き import と rollback。

### Should: 初回切替後の近接リリース

- 配信前チェックリストと readiness 表示。
- 失敗した Twitch 操作の安全な再試行。
- 操作監査ログと相関 ID。
- 2 配信以上の比較と指標フィルター。
- 設定・ルール・プリセットの export/import。
- 保持期間とプライバシー設定。

### Could: 利用実績を見て判断

- PWA/offline shell。
- キーボードショートカットのカスタマイズ。
- 通知音、ブラウザ通知。
- 複数チャンネル対応。
- plugin architecture や第三者拡張 API。

### Won't: この再構築では扱わない

- OBS の起動・録画・停止・状態表示。
- secretary-bot への push 通知または問い合わせ。
- VOD を OBS archive へ移行する機能。
- 配信ソフト自体の制御。
- クラウド SaaS 化、マルチテナント、チーム権限管理。
- 複数コンテナ、Redis、外部 DB を前提にする構成。
- image publish、Portainer deploy、production data の直接移行作業。

## 7. 機能要件

### FR-01 ライブ状態

- Worker が受け入れた実 Twitch 観測だけを current stream とする。
- debug/ignore mode は内部テスト挙動に限定し、外部 status には公開しない。
- offline 確定と Bot 無効化で external snapshot を消去する。
- 各状態に `observed_at` と freshness を持たせる。

### FR-02 配信準備と channel update

- preset 適用前に変更内容を差分表示する。
- title/category/tags の更新結果を Twitch 応答に基づき表示する。
- 同じ送信を連打しても二重操作にならない UI 状態を持つ。
- preset の social tags と現在の title/category から告知文を生成し、利用者の click で X intent を
  開く。X API、X credential、自動投稿は導入しない。

### FR-03 自動チャット

- game scope、interval、最低コメント数、message、有効状態を保持する。
- 実行条件、次回評価時刻、最後の結果を表示する。
- rule の永続 ID を使い、並べ替えで実行状態が別 rule へ移らない。
- Twitch rate limit と 1 channel あたりの送信間隔を順守する。

### FR-04 予想

- Twitch 上の active prediction を唯一の正とする。
- Affiliate/Partner 条件、scope 不足、同時 1 件制約を具体的に表示する。
- start/resolve/cancel は結果確認後にローカル状態を確定する。

### FR-05 コミュニティ

- 現セッションの viewer snapshot と累積 viewer record を区別する。
- メモは viewer ID に紐付け、表示名変更に耐える。
- shoutout は対象、実行者相当、時刻、結果を operation log に残す。

### FR-06 分析

- UTC 保存、JST 表示を原則とする。
- Bot が観測した配信と Twitch API から同期した配信を区別する。
- 部分データや欠測を 0 と誤表示しない。
- 配信一覧から詳細、比較、VOD へ同じ stream ID で移動できる。

### FR-07 VOD archive

- manual、bulk、automatic の起点を job に記録する。
- queued/running/cancelling/succeeded/failed/cancelled を区別する。
- download と file delete は独立操作とし、対象パスを検証する。
- 自動 download は `enable_vod_download=false` を初期値とする。

### FR-08 設定と認証情報

- UI が既存 token 値を API response や HTML へ返さない。
- scope と接続テスト結果を、単なる成功/失敗ではなく機能別に示す。
- 非秘密設定と credentials を export/backup 上で分離する。
- `bot` と `broadcaster` を actor-bound credential とし、各 token の subject user ID が設定された
  actor ID と一致することを検証する。
- 両 actor が同じ Twitch user ID の場合は一つの credential record を共有できる。異なる場合は
  access/refresh/scopes/expiry を独立して管理し、暗黙に片方へ fallback しない。

## 8. 非機能要件

### NFR-01 可用性と復旧

- Twitch 一時障害、WebSocket 切断、429、401、VOD job 失敗から自動または手動で復旧できる。
- EventSub message ID による重複排除を行う。
- process restart 後に永続 job と stream history の整合性を回復する。

### NFR-02 性能予算（初期目標）

実機計測で確定する暫定予算とする。

- warm LAN で cached read API p95 200 ms 未満。
- dashboard LCP 2.5 s 未満、操作応答 200 ms 未満（ネットワーク完了は別表示）。
- live polling payload は通常 50 KB 未満。
- idle 時の継続的な disk write を避け、時系列統計は batch 化する。
- 一覧は 100 件を超えたら server-side pagination を使う。

### NFR-03 アクセシビリティ

- WCAG 2.2 AA を目標にする。
- キーボードのみで全操作可能にする。
- focus、dialog、tab、table sort、live region を標準セマンティクスで実装する。
- 色だけで状態を伝えず、reduced motion と 320 px 幅を支援する。

### NFR-04 セキュリティとプライバシー

- Cloudflare Access/LAN の既存外部境界を変えない。
- mutation は同一 origin + CSRF を要求し、token/secret を log に残さない。
- viewer/chat data の export・保持・削除範囲を明示する。
- path、stream ID、数値、配列長、テキスト長を server 側で検証する。

### NFR-05 互換性

- `linux/amd64` と `linux/arm64` で同一 image を build できる。
- `/app/data` と `/app/downloads` 以外へ永続状態を置かない。
- 外部 status consumer は v2 切替を意識せず利用できる。

## 9. 成功指標

実装前に baseline を採取し、v2 pilot で比較する。

| 指標 | 目標 |
|---|---|
| 配信前の preset 適用から readiness 確認まで | 3 操作以内 |
| live 状態・Bot 状態・接続異常の判別 | 初期画面だけで可能 |
| 主要 5 操作の keyboard 完遂率 | 100% |
| request-time 外部通信を行う snapshot API | 0 件 |
| EventSub 重複による二重集計/二重操作 | 0 件 |
| migration rehearsal の record mismatch | 0 件 |
| 自動 VOD の既定 | 無効 100% |
| Critical/High accessibility finding | 0 件 |

主要 5 操作は、preset 適用、Bot 有効化、shoutout、prediction 開始/確定、VOD 再試行とする。

## 10. Open questions

実装を止める質問だけを先に解決する。

1. v2 でも単一チャンネル・単一オペレーター専用でよいか。
2. Bot と broadcaster は現在同一アカウントか別アカウントか。v2 は両方を扱うが、scope と
   EventSub session の構成確認に必要となる。
3. 解決済み（2026-09-06）: チャット本文は無期限保存・手動削除。
   [今回の保存方針](07-recording-and-workflows.md)を参照する。
4. UI theme は system/light/dark の 3 状態にするか、dark 固定運用を残すか。
5. legacy IRC を初回切替まで残すか、EventSub chat への完全移行を先に行うか。
6. Cloudflare Access が付与する identity を画面に表示する必要があるか。

複数チャンネルやアプリ内ユーザー認証を求める場合は、データモデルとセキュリティ境界が
変わるため、最初の実装 handoff より前に決定する。
