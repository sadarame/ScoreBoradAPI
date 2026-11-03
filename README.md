# ScoreBoradAPI

NBA・NPBを収集して、既存Flutterアプリ／ウィジェット向けFirestoreコレクションを更新するCronバッチ。

## 実行

```sh
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
python main.py
```

認証は `GOOGLE_APPLICATION_CREDENTIALS` のサービスアカウントファイル、未指定時は既存の同階層JSONを利用する。鍵をGitへ追加しない。実稼働はPython 3.7のため互換性を維持した復旧修正。ランタイム更新は別途実施する。

## 取得と互換性

- NBA公式CDNがHTTPエラー・JSON不正・古い日付の場合、ESPN scoreboardへフォールバック。
- ESPNは米東部06:00を境にした試合日を明示指定する。深夜の終了直後にスコアが消えないようにする。
- 取得元の略称は既存 `TB_TEAM_MST.searchNM` に変換する（UTAH→UTA、NY→NYK、CHA→CHO等）。
- NPBは当日セクションの日付と明示された「試合はありません」を検証。HTML不正を0件成功にしない。
- タイムアウトは接続5秒・読取15秒、最大3回。401/403/404は即座に失敗する。
- 片方のリーグが失敗した場合は前回値を保持して他方だけ更新。両方失敗時は書き込まない。
- 旧5フィールドと文字列スコアは維持し、league/matchId/source/update_dateを追加。
- リーグ別・統合コレクションと更新状態を1バッチでコミット。削除だけが先に見える状態を防ぐ。
- `TB_SCORE_UPDATE_STATUS` に成功時刻・件数・状態を記録。失敗時は最終成功時刻を維持。
- 同一配備ディレクトリでのCron重複をflockで防ぐ。異なるホストからの同時実行はしない。
- 初回の旧レコード分類はチームマスターを使う。判別不能なレコードは削除せず保持する。

## 配備

FTP設定は既存ローカル設定を使い、転送時は証明書検証付きFTPSを利用。新コードを専用リリースディレクトリに先に配置し、ルートmain.pyだけをrenameで切り替える。既存のPython実行パス・認証JSON・Cron設定は維持する。直前のmain.pyを非公開バックアップに保存し、切り戻し時は元の入口を復元する。

## 検証

`tests/fixtures` は2026-10-25の公開スコアを最小限抽出した取得形式の回帰テスト用データ。現在のスコアとして配信しない。依存更新時はPython 3.7構文確認と対象環境でのCron実行を確認する。

## チームスタッツ・JST・サッカー（追加実装）

- `startsAt` はタイムゾーン付きUTC時刻、表示は端末設定に依存せずJST（UTC+9）。`update_date` と試合開始時刻を混同しない。
- NBA: 勝敗、リバウンド、アシスト、FG/3P/FT成功率など、取得元が返す指標だけ保存。NBA公式のboxscore取得失敗時もスコア更新は継続。
- サッカー: J1 / EPL / ラ・リーガ / ブンデスリーガ / セリエA / リーグ・アン。チーム成績（勝ち−引き分け−負け）、シュート、枠内シュート、支配率等。提供されない指標は空欄。NPBの詳細スタッツは未対応。
- ESPNの公開エンドポイントを利用する。正式に保証された契約APIではないため、運用時は利用条件・取得頻度・継続性を確認する。J1チーム名は取得元の英語表記。
- サッカーは前日と当日をそれぞれ日付指定して取得し、開始時刻のJST日付が当日の試合だけを表示。日付範囲指定は実応答で400になるため使用しない。NBAは従来の米東部06:00切替を維持し、各試合のJST開始日時を明示する。
- `SOCCER_LEAGUES=J1,EPL` で取得対象を絞れる。未指定は6リーグ、空文字は無効。
- `TB_TEAM_STATS/{sha256(league + ':' + searchNM)}` にシーズン成績と最後に取得した試合を保存。試合のない日も参照できる。未開始試合で最後の試合を上書きしない。シーズン成績は取得時点の値であり独自に集計しない。
- サッカーチームの登録は `python sync_soccer_teams.py` でプレビューし、`python sync_soccer_teams.py --publish` で既存チームを削除せず追加・更新。初回配備時、および昇降格・シーズン変更時に実施する。新規リーグは統合コレクションだけに保存し、旧NBA/NPB形式を維持。

## 試合のPUSH配信

2026-11-01: 本番の旧リリースに通知処理が存在しなかったことを確認し、NBA・NPB・サッカー6リーグの通知処理を配備。アプリの通知画面で開始／中間／終了を個別に有効にする。初期値はOFF。OSの許可が必要。

1. 追加Pythonファイルを含めて配備する。Cronは単一ホストで1分程度の間隔にする。既存のflockを維持する。
2. チームカタログを同期する。Firestoreの既存ルールへ `TB_TEAM_STATS` の読み取りを追加し、クライアント書き込みは禁止する。`TB_NOTIFICATION_STATE` / `TB_NOTIFICATION_OUTBOX` は管理サーバー専用でクライアント読み書きを許可しない。既存ルールの上書きはしない。
3. Firebase Cloud Messaging APIとサービスアカウントの送信権限を確認する。iOSではFirebase側のAPNsキー、アプリ署名のPush capabilityを確認する。Android 13以降は通知権限が必要。
4. Cron環境に `ENABLE_PUSH=1` を設定する。未指定では送信しない。初回観測は基準状態として保存し、過去の終了通知をまとめて送らない。
5. 実機で通知許可、3種類の通知、チーム解除、アプリの起動中／バックグラウンド／終了状態を確認する。ローカルテストは実端末への到達を保証しない。

- 配信先は `score_NBA_{searchNM}_{start|halftime|final}`。ホーム／アウェイのOR条件で1回送信するため、両チームを登録しても同じ通知が二重にならない。
- 開始は試合前→第1Q、中間は試合中→ハーフタイム、終了は試合中／ハーフタイム→終了を検出。予定時刻だけでは開始と判断しない。
- 10分超の観測中断後は過去イベントを追送しない。ハーフタイムを取得できず第3Qに進んでいた場合も、中間結果を捏造して送らない。
- Firestoreトランザクションでイベントを記録・占有。配信元切替でもNBA/ESPNの異なるIDによる二重送信を防ぐ。
- FCMへの送信とFirestore更新は原子的ではない。送信中クラッシュや応答不明の失敗は `sending` / `failed` として残し、自動再送しない（重複防止を優先）。運用で監視する。FCM自体の到達保証・厳密な即時性はない。
- 未送信イベントは5分で失効。`expiresAt` をTTLとして管理コンソールで設定すれば状態／履歴を7日で削除できる。
- ソース: [Firebase Flutter FCM](https://firebase.google.com/docs/cloud-messaging/flutter/receive-messages)、[トピック条件による配信](https://firebase.google.com/docs/cloud-messaging/send-topic-messages)。

## 保存型ニュースバッチ（2026-10-30）

- `news_batch.py` はスコア更新とは別プロセス。既存Cron入口から起動し、専用flockと最終実行時刻で15分間隔に制限する。スコアの取得・通知処理を待たせない。
- Hoops Rumors、バスケットボールキング、ベースボールキング、サッカーキングの直接RSSをページングし、過去7日分を保存。概要だけの項目は公開ページから本文を取得できた場合のみ採用。NBA docomoの試合配信・見逃し配信など記事でないURLは除外する。
- `TB_NEWS_FEED/current` は軽量一覧（本文・画像バイナリを含めない）。`TB_NEWS_ARTICLES/{id}` は段落化した本文、`TB_NEWS_IMAGES/{sha256}` は検証済み画像バイナリ（600KB以下）。本文・画像の保存後に一覧を公開する。
- 画像は記事ページのog:image等から取得する。存在しない・未対応・サイズ上限を超える画像は捏造せず画像なしとする。一時的な画像取得失敗は6時間後に再試行する。
- HTTPSの証明書・ホスト名検証とIP固定接続、私設IP除外、リダイレクト上限、サイズ上限を適用。制限付き記事を回避して取得しない。
- 配信元の失敗時は期間内の保存済み記事を維持する。全配信元失敗時は一覧を上書きしない。7日を超えた本文・画像を一覧更新後に整理する。
- アプリは一覧を1回取得し、チームIDで分類。本文・画像は必要時に保存先から読み、端末内で再利用する。英語の見出し翻訳は端末内に保存して再利用し、本文翻訳は対応iPhoneで行う。本文閲覧のためにブラウザを開かない。
- `TB_NEWS_STATUS/collector` に最終試行・成功・件数・画像件数・警告を記録する。

検証: `.venv/bin/python -m unittest discover -s tests -p test_news_batch.py`。
配備: `.venv/bin/python tools/deploy_news.py --config .vscode/sftp.json`。既存Cron入口をバックアップし、ニュースワーカーの呼び出しだけを追加。切り戻しは出力されたバックアップを `main.py` に戻す。認証JSONは既存のサーバーファイルを使い、新しい公開ディレクトリにコピーしない。

### まとめ記事（2026-10-30）

ニュースバッチは NBAまとめ！、バスケまとめ・COM、ドメサカブログ、ツバメ速報、かーぷぶーんも取得します。RSS 1.0/RDF と RSS 2.0 に対応し、本文を取得できた記事だけ `kind: matome` として保存します。RDF配信元は最新フィードを定期取得し、保存済み記事を7日間保持します。通常ニュースと同じ一覧に表示し、選択チームのカラータグと「まとめ」タグを付けます。日刊やきう速報はHTTPS接続エラーのため取得元に含めていません。


### 通知復旧（2026-11-01）

- 本番リリース: `.scoreboard-releases/20261101-notifications-9f0d44f8f4c790cf`。既存ニュースワーカーの起動処理と資格情報の参照先を維持。
- Cron起動時にサーバーの認証でFCM dry-runを実行し、成功後に `ENABLE_PUSH=1`。dry-runは端末へ送信しない。失敗時は通知を有効化せずスコア収集を継続し、ログに記録する。
- `notification_runner.py` は既存5分間隔Cronの各起動から最大5回、1分間隔で収集する。リリース共通のflockで重複起動を抑止し、処理遅延時に追いつくための連続取得をしない。
- `health.json` に最終観測時刻、通知有効状態、リーグ別件数、失敗を保存。`collector.log` にFCM検証と送信件数を記録。ログは2MBで世代交代。
- 初回観測では過去通知を送らない。送信失敗はoutboxに残し、曖昧なエラーの自動再送はしない。実試合の通知生成・FCM受付と、iPhoneでの表示確認は別の検証。
- 復旧前への切り戻し: 上記リリース内の `launcher-before.py` をルート `main.py` に戻す。1分確認だけを戻す場合は `launcher-before-minute-25512f4b79570e40.py` を戻す。切り戻し時も起動済みの最大5分の処理は完了まで動く。
- 通知APIの現行修正先はこの `ScoreBoradAPI`。別フォルダ `scoreboard-api-notifications-20261028` にあった実装と回帰テストを取り込み済み。

運用確認: `.venv/bin/python tools/check_notifications.py`（既存FTPS設定で稼働リリース、FCM検証結果、直近25行のログを読み取る）。回帰検証: 72件成功、配備対象全7ファイルのPython 3.7構文確認済み。

本番確認: 2026-11-01 11:00 JSTにFCM dry-run成功。11:05:02→11:06:01 JSTに約60秒間隔の観測と31試合・8リーグの成功を確認、`pushEnabled: true`、`failures: {}`。初回の31試合から過去通知を生成していない（outbox 0件）。この時点では新しい開始/中間/終了遷移の実配信とiPhoneでの表示は未確認。

2026-11-01 通知文面: 日時を「11月01日 11:00」形式にし、JST表記を削除。NBAチーム名はアプリと共通の日本語名、リーグ名は利用者向け表記へ変更。既に動作中のワーカーは次回Cron起動から新文面を使用。72件の回帰テスト成功。

2026-11-01 追加変更: 通知本文から日時・開始時刻不明の表示も削除。チーム名とスコアを表示し、NPBの6回表で検知した中間通知にはスコアの時点の補足を維持。72件の回帰テスト成功。本番反映済み、起動中ワーカーは次のCron起動時から切り替わる。

## 問い合わせ受付（実装済み・未配備）

`contact_api:application` は収集Cronとは別のWSGI受付。宛先は `sadarame@gmail.com` 固定。
HTTPSで公開し、リバースプロキシでPOST本文を32KBに制限する。
必要なサーバー環境変数は `CONTACT_SMTP_HOST`、`CONTACT_SMTP_PORT`（既定465、TLS）、
`CONTACT_SMTP_USER`、`CONTACT_SMTP_PASSWORD`、`CONTACT_SMTP_FROM`、
`CONTACT_RATE_DB`（公開領域外の書き込み可能なSQLiteファイルの絶対パス）。
SMTP資格情報をアプリやGitに保存しない。送信元はSMTPサービスで認証済みのアドレスを使う。
`REMOTE_ADDR` は信頼するプロキシだけで実クライアントIPへ復元する。任意の転送ヘッダーは信頼しない。
1 IP当たり毎時5回、全体毎時100回に制限（失敗も含む）。DBにはIPと時刻を保存し次回受付時に1時間超を削除。
SMTP受付成功後のみ成功応答。SMTP応答の通信断は送信済みの可能性があるため自動再送しない。

Flutterビルドに `--dart-define=CONTACT_API_URL=https://<公開ホスト>/<受付パス>` を指定。
未指定時は送信不可を表示し、送信成功とは扱わない。
アプリは返信先・本文・OS種別／バージョン・アプリバージョン／ビルド番号のみを送る。
本番公開前にSMTP設定、HTTPS受付の設置、Gmailへの到達確認、両OSでのビルド確認が必要。
検証: `python3 -m unittest discover -s tests -p test_contact_api.py`。

### SMTP契約がない場合（ロリポップ Sendmail）

既定の配送方式は `CONTACT_TRANSPORT=sendmail`。SMTPの資格情報は不要。
`CONTACT_FROM` にサーバーで使用可能な送信元アドレスを設定する。
`CONTACT_SENDMAIL_PATH`（既定 `/usr/sbin/sendmail`）はホスト仕様に合わせる。
SMTPを使用する場合だけ `CONTACT_TRANSPORT=smtp` と前述のSMTP変数を設定する。
宛先は引数に固定し、ユーザー入力をシェルや送信先引数に渡さない。

常駐WSGIのないホスト用に `contact.cgi` を追加。専用ディレクトリへ
`contact.cgi`（実行権限744）、`contact_api.py` を配置する。
`.contact-private/.htaccess` に `Require all denied` を設置したうえで、
同ディレクトリに `contact-settings.json`（CONTACT_FROM等）とレート制限DBを保存。
HTTPで非公開領域が403になることと、CGIが実行されソースが配信されないことを確認してから接続する。
Sendmail受付はGmail到達の保証ではないため、受信箱での到達確認が必要。
公式仕様: https://lolipop.jp/manual/hp/cgi/

### 2026-11-01 本番接続

- HTTPS受付: `https://golira-pochette.com/scoreboard-contact/contact.cgi`。
- `tools/deploy_contact.py` で既存サイト・Cronとは独立した `/scoreboard-contact` に配置。
- ロリポップのSendmailを使用。送信元 `scoreboard@nikudango.ciao.jp`、宛先 `sadarame@gmail.com` 固定。
- 設定JSONとPythonソースのHTTPアクセスは403、GETは405、不正入力POSTは400、テスト送信は200を確認。
- 2026-11-01 20:39 JST、接続確認メール1通が宛先Gmailへ到達。ただしSPAMラベルだったため通常受信トレイへの配信は未確認。送信元認証・迷惑メール判定は今後の運用確認事項。
- FlutterのCONTACT_API_URL既定値を上記URLへ設定済み。実機へのインストールは未実施。
- 本文や返信先をサーバーに永続保存せずSendmailへ渡す。レート制限DBは非公開領域。
- 検証: Python問い合わせテスト6件、Flutter問い合わせ・既存設定テスト14件成功。変更Dartの静的解析成功。iOS Release署名なしビルド成功。
- Android: 標準Gradle 8.10.2取得が停滞したため、そのビルドプロセスを停止。既存キャッシュのGradle 8.12でオフライン検証したが `firebase-analytics:21.6.1` 未キャッシュで失敗。Androidビルド確認は未完了（既存依存関係の取得が必要）。
- 2026-11-01 20:52 JST: 問い合わせ機能と本番URLを含む署名付きiOS ReleaseビルドをYosukeのiPhone 15へ上書きインストール・起動成功。実機画面からのフォーム送信は未確認。ユーザーからGmailの迷惑メール設定を解除済みとの報告あり。
- 2026-11-01 21:46 JST: ユーザー指示で返信先メールアドレスの入力・送信・Reply-To設定を削除。本文と自動取得する環境情報のみで受付。本番APIを更新し、Flutterテスト・静的解析・APIテスト5件成功。署名付きReleaseをiPhone 15へインストール・起動成功。

## 通知のFirestore上限障害対策（2026-11-03）

実機から取得した設定で `score_NBA_SAS_start/halftime/final` の登録・同期完了を確認。
当該端末に限定したFCMテスト通知は本人が受信確認。本番ではFirestoreの
`429 Quota exceeded` によりスコア保存で停止し、後段の通知判定まで進めなかった。

配備先: `.scoreboard-releases/20261103-push-resilience-b4d3a0ac3fa011cb`。
`tools/deploy_notification_resilience.py` は現行コレクターとニュース起動処理を保持し、
通知・保存処理だけを切り替える。既存のNPB取得エラーは別件として残る。

- `SCOREBOARD_STATE_PATH` が設定された本番は `DurableNotifications.py` を使用。
  通知の状態・outbox・送信前の確保を、非公開の `.scoreboard-state/collector.sqlite3` に
  永続化する。Firestoreは監査用ミラー。Firestoreの障害中も判定・配信を継続する。
- SQLiteは**リリースを跨いで保持する**。削除・初期化すると監視の基準を失う。
  単一Cronホストと共通 `.minute-poll.lock` が前提。複数ホストで同時運用しない。
  バックアップはSQLite backup APIまたはワーカー停止中に取得する。
- 送信前に `sending` を確定し、FCM成功後に `sent` を記録する。
  不明な送信結果や送信中のプロセス停止は自動再送しない（重複防止を優先）。
  初回観測・10分超の観測欠落では過去通知を再生しない。
- Firestoreの状態ミラーは状態変化時のみ。送信履歴は未反映分を後で同期する。
  SQLiteの `records` テーブルで namespace が `TB_NOTIFICATION_STATE` /
  `TB_NOTIFICATION_OUTBOX` の行を参照。valueは日時を型付きJSONで保存する。
- `EfficientFirestore.py` は確認済みスナップショットをローカルにキャッシュし、
  内容が変わった文書だけを保存。FirestoreのRPCは8秒・自動再試行なし。
  エラー後は15分間保存を延期し、通知監視を妨げない。
- 既存アプリの5分鮮度判定に合わせ、統合スコア文書だけは最大4分で更新時刻を更新。
  スタッツは5分間隔、取得状態の時刻は15分間隔。全件の削除・再追加を廃止。
  無料枠内を保証するものではなく、利用者の読み取りやニュース処理も別途影響する。
- `health.json` は保存に失敗しても更新し、`notificationDelivery` を記録する。
  `pushEnabled: true` だけを送信成功とみなさない。

検証: 全87件成功。最終の時刻更新調整は関連10件を再実行して成功。
実機の直接通知は受信確認済み。実試合の開始・終了通知は別途送信履歴で確認する。

本番確認: 11月3日11:20:02→11:21:02 JST、NBA15試合の監視継続を確認。
Firestore 429継続中でも通知処理は成功し、送信失敗0件。
その間の送信イベントは0件であり、実試合の次回遷移の実機受信までは未確認。

### 更新間隔の追加改善（2026-11-03）

ユーザー指定により、通知監視1分とスコアのFirestore保存5分を分離。
本番リリースは `.scoreboard-releases/20261103-push-resilience-063fcc4a1bcd9895`。
成功保存後5分未満は保存を省略し、通知の検知・送信は毎分続行する。
統合スコアの鮮度更新も5分（前記4分を置き換える）。アプリの再取得は1分、
古いデータの表示判定は10分に調整。87件の既存検証と、追加の分離検証を含む
関連11件、アプリ関連13件が成功した。

アプリ側の更新間隔変更は2026-11-03 11:39 JSTにReleaseビルドを
YosukeのiPhoneへ上書きインストール・起動成功。
