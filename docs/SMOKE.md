# 手動スモークテスト

- 対応: [DESIGN.md](DESIGN.md) §11-3、[M2_PLAN.md](M2_PLAN.md) SM-01
- 本書は [M1_PLAN.md](M1_PLAN.md) §5(M1 受け入れ基準)の後継。M1 の必須項目 #1〜7 はすでに PASS 済みのため、本書ではそれらを踏襲しつつ M2 で追加された `get_state` / `reset_session` のシナリオを加え、正式なリリース前チェックリストとして独立させた。
- 実行環境: ParaView GUI(実機)。builtin と pvserver 接続の両方を対象とする。
- 操作の大半は [tests/smoke/run_smoke.py](../tests/smoke/run_smoke.py) が自動で行う(MCP クライアントとしてサーバーに接続し、ツール呼び出しの列を流して PASS/FAIL を印字する)。**人間が担当するのは ParaView 側の GUI 操作(マクロ起動・pvserver 接続・ウィンドウを閉じる等)と、スクリーンショットの目視確認のみ**。
- 実施したら本書末尾の記録表に日付・ParaView 版数・結果を記入する。

## 事前準備

1. `uv sync` 済みであること。
2. `PARAVIEW_MCP_PORT` を必要なら決めておく(既定 9911)。
3. `tests/smoke/run_smoke.py` は `paraview-mcp` サーバーを自分で子プロセス起動する(`uv run paraview-mcp` 相当)ので、別途サーバーを起動しておく必要はない。ブリッジ(ParaView 側)だけ手動で起動する。

## シナリオ① builtin: 基本 5 ツール

1. ParaView を起動する(pvserver には接続しない)。
2. `bridge/paraview_mcp_bridge.py` を **Macros → Import new macro** で登録し、実行する。
   - 期待結果: Shell に `listening on 127.0.0.1:9911` と `bridge active (first tick fired)` が出る(M1_PLAN §5 #1)。
3. `python tests/smoke/run_smoke.py --scenario basic` を実行する。
   - 内部で `bridge_status` → `execute_python`(Sphere 生成)→ `get_screenshot`(ファイル保存)→ `get_state("summary")` → `get_state("arrays")` → `get_state("full")` → `reset_session` の順に呼び、各応答を検証して PASS/FAIL を印字する。
   - 期待結果: 全項目 PASS。`get_screenshot` が保存した JPEG ファイルのパスが表示されるので、**目視で球が正しく描画されているか確認する**(自動検証は「有効な JPEG であること」までで、見た目の正しさは人間の担当)。

## シナリオ② タイムアウトと直後の回復

1. シナリオ①に続けて(ブリッジは起動したまま)、`python tests/smoke/run_smoke.py --scenario timeout` を実行する。
   - 内部で `execute_python("import time; time.sleep(5)", timeout_s=2)` を呼びタイムアウトを起こした直後、`execute_python("1+1")` を呼ぶ。
   - 期待結果: 1 回目は `get_state` を促すタイムアウト文言のエラー(DESIGN.md §10)。ParaView 側は `time.sleep` が終わるまで数秒フリーズする(仕様どおり)。2 回目は正常に `value: 2` が返る。

## シナリオ③ ParaView 終了時のガイダンス

1. シナリオ①または②で使った ParaView を**終了する**。
2. `python tests/smoke/run_smoke.py --scenario disconnected` を実行する。
   - 内部で `bridge_status` を呼ぶ。
   - 期待結果: `connected: false` かつ `guidance` に「マクロを実行せよ」という趣旨の文言。**MCP サーバー自体はクラッシュせず正常終了する**(DESIGN.md §10 の「サーバー自体は決して落とさない」)。

## シナリオ④ ブリッジを載せた view を閉じた際の診断

1. ParaView を起動し、マクロを実行してブリッジを起動する。
2. RenderView を(すべて)閉じる。あるいは新規に開いた別の RenderView をアクティブにしてから元の view を閉じる。
3. `python tests/smoke/run_smoke.py --scenario disconnected` を実行する。
   - 期待結果: TCP 接続は成立するが `ping` が無応答、またはタイムアウトになる(タイマーが死んでいるため)。DESIGN.md §10「接続は成立するが ping 無応答」のケースとして、マクロの再実行を促す文言が出ることを確認する。
   - 注記: v1 ではタイマー消失の自動検出・復旧は行わない(DESIGN.md §12 既知の制約)。ここでは「異常時にユーザーへ何が案内されるか」を確認する。

## シナリオ⑤ pvserver 接続

1. `pvserver` を起動する。
2. ParaView GUI を起動し、File → Connect で pvserver に接続する。
3. **ブリッジはマクロ登録ではなく、`bridge/paraview_mcp_bridge.py` の内容を View → Python Shell に直接貼り付けて実行する。**
   - **マクロ登録経由での起動は使わないこと**: pvserver 接続下でマクロとして実行すると ParaView が確実にセグメンテーション違反で落ちる、確認済みの ParaView 側の問題がある(M1_PLAN.md §5 #8 に gdb バックトレース・切り分け実験の詳細を記録済み。本プロジェクトのコードの欠陥ではない)。Shell への直接貼り付けはこの問題の影響を受けない。
4. `python tests/smoke/run_smoke.py --scenario basic` を実行する。
   - 期待結果: シナリオ①と同一の結果に加え、`bridge_status` の `session_type` が `client-server` になっていること、`server` に pvserver の接続先が入っていること。

## シナリオ⑥ get_state / reset_session(M2 新規)

シナリオ①の `--scenario basic` に含まれるが、個別に着目して確認する:

- `get_state("summary")`: `execute_python` の `state` と同じ形の要約が、追加の実行なしで返ること。
- `get_state("arrays")`: Sphere の `Normals` 点データ配列が名前・成分数・レンジ付きで返ること。
- `get_state("full")`: 上記に加え `bounds` / `n_points` / `n_cells` / `properties`(`Radius` 等)が返ること。
- `reset_session()`: 呼び出し後、`get_state("summary")` の `sources` が空になっていること。

## 記録表

| 実施日 | ParaView 版数 | 環境 | ①builtin | ②timeout | ③disconnected | ④view閉鎖 | ⑤pvserver | ⑥get_state/reset | 備考 |
|---|---|---|---|---|---|---|---|---|---|
| | | | | | | | | | |
