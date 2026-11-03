# NBA公式 欠場・出場情報ワーカー（未配備）

`feature/player-availability` で独立開発。既存main.py/Cron/通知/Firestoreを変更しない。
申請版アプリへの影響はない。API元の作業フォルダも変更していない。

## 取得・データ契約

- 公式PDF: https://ak-static.cms.nba.com/referee/injury/
- 公式案内: https://official.nba.com/nba-injury-report-2026-27-season/
- 15分単位の公式ファイル名から、現在〜2時間前の最大9候補を最新順に確認。
  一覧ページは自動取得で403となるため使用しない。PDFも403等なら失敗とし、回避しない。
  404の場合だけ一つ前の候補へ進む。
- PDFのページサイズ、見出し、時刻、列と行の境界を確認して抽出する。
  理由の折り返し・ページをまたぐ理由/チームを保持する。
  不明な形式・状態・チーム・欠落があれば公開更新を止める。
- `schemaVersion: 1, league: NBA, reportAt, fetchedAt, sourceUrl, games`。
  時刻はUTC ISO8601。`reportAt` はPDFの公式発表時刻。取得時刻と混同しない。
- `games[]`: `gameDateEt` (YYYY-MM-DD, 米東部の試合日), `home`, `away`, `teams`。
  `teams[略号]`: `team`, `submission` (`pending`/`submitted`), `players`。
  `players[]`: `name` (原文), `status` (原文), `reason` (原文)。
- 欠場情報は病気以外の休養・チーム非帯同・Gリーグ派遣なども対象。
- 失敗時は前回ファイルを保持。古い発表への巻き戻しは拒否。
  ファイルは同一ディレクトリ内でatomic replace。CLIはflockで同時実行を抑止。
- 最新PDFだけのスナップショット。過去の全試合をアーカイブする機能はない。
  対象試合が消えていても、別の試合の情報を流用しない。

## ローカル検証

専用virtualenvで `pip install -r requirements-availability.txt`。
Python 3.7対応のpdfplumber 0.9.0を使用。今回の実行検証はPython 3.10。
既存本番virtualenvへのインストールはしない。PDF解析には画像変換は不要。

```
python -m unittest discover -s tests -p test_player_availability.py
python PlayerAvailability.py --output /tmp/availability/nba.json
python PlayerAvailability.py --pdf tests/fixtures/nba-injury-20260108.pdf --source-url https://ak-static.cms.nba.com/referee/injury/Injury-Report_2026-01-08_11_00PM.pdf --output /tmp/availability/historical.json
```

テストfixtureは上記公式PDFと `Injury-Report_2026-11-03_12_30AM.pdf`。
本文からの短い行抽出を再現するための原本。個人端末情報や資格情報は含まない。

## 配備時に行うこと（今回は未実施）

1. 専用virtualenv・ワーカーを新しい非公開ディレクトリへ配置。
2. テストと手動取得を行い、生成したJSONのreportAtと両チームを確認。
3. 読み取り専用HTTPSでJSONだけを配信。例 `/availability/nba.json`。
   Cache-Controlは最大300秒。Webプレビュー利用時は許可するオリジンだけCORSを設定。
   ワーカー・仮想環境・ログ・資格情報は公開しない。隠しlockファイルも配信対象外。
4. 専用Cronを毎時2,17,32,47分に設定。既存のスコア/通知Cronには追加しない。
   stderrを非公開ログへ保存し、終了コードとreportAtで更新失敗を監視する。
5. アプリを `--dart-define=NBA_AVAILABILITY_URL=https://<host>/availability/nba.json` 付きで検証。
   申請版の差し替えを行わず、次回版としてバージョン・ビルド番号を決めて提出。

停止時は専用Cronのみ停止。スコア/通知状態やSQLiteに触れない。
