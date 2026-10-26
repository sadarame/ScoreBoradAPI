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
