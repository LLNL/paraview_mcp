# M2 要件設計・テスト設計

- Status: 実装中(2026-07-21)— §6 実装順序 1〜4(コード・テスト・CI 定義・SMOKE.md/run_smoke.py の作成)が完了。unit 96 件+integration 12 件(計 108 件)green、ruff clean。integration 実装中に**サーバー側の実バグ**を発見・修正: FastMCP は `-> dict` という無引数の型注釈では構造化出力(`structuredContent`)を生成しない(`dict[str, Any]` のようにパラメータ化された型が必要)。unit テストは server.py の関数を直接呼ぶため気づけなかった欠陥で、execute_python / bridge_status を含む既存 4 ツール全部に影響していた(src/paraview_mcp/server.py の戻り値注釈を修正)。
  - `run_smoke.py` 自体は ParaView 6.1.1 実機の standalone pvpython(headless)を相手にエンドツーエンドで動作確認し、その過程で 2 件のスクリプト側バグ(env 未継承・`UpdatePipeline()`/`Show()` 未呼び出しでスクリーンショットが空になる)を発見・修正した。ただし **GUI でのマクロ登録・pvserver 接続・view 閉鎖など、SMOKE.md 本来のシナリオ①〜⑤(人間が ParaView GUI を操作する部分)は未実施** — 本セッションはヘッドレス環境のため。§5 の #3(GitHub Actions 上での integration CI green)も push が必要なため未実施。いずれもユーザー側での実施が必要
- 対応マイルストーン: [DESIGN.md](DESIGN.md) §13 M2(堅牢化)
- 前提: M1 完了([M1_PLAN.md](M1_PLAN.md)。§5 必須 #1〜7 PASS、unit 76 件 green)
- 本書の位置づけ: DESIGN.md が仕様の正。本書は M2 で実装する範囲の確定・要件の実装単位への分解・テスト設計のみを扱い、仕様の詳細は DESIGN.md の節番号で参照する。

## 1. スコープ

### 1.1 方針: ブリッジには一切手を入れない

M1 でブリッジはワイヤプロトコル v1 を完全実装して凍結した(M1_PLAN §1.1)。M2 の新機能(get_state / reset_session)はすべてサーバー側の定型スニペット+ツール定義で実現し、`bridge/paraview_mcp_bridge.py` は 1 行も変更しない。**ユーザーが配布済みマクロを差し替えずに M2 サーバーへ更新できること**を検収条件に含める(§5 #5)。

### 1.2 方針: pvserver+マクロ登録セグフォ(M1_PLAN §5 #8)は運用で吸収する

M1 受け入れで見つかった「pvserver 接続下でマクロ登録経由でブリッジを起動すると ParaView がセグフォ」は、未変更の M0 スパイクでも再現する ParaView 側の問題と切り分け済み(M1_PLAN §5 #8)。本プロジェクトのコードでは修正できないため、M2 では以下で吸収する:

- SMOKE.md の pvserver シナリオは **Python Shell 貼り付け起動を正式手順**として記載する(SM-01)
- README に回避策を明記(改稿済み、2026-07-19)
- integration CI は standalone モード(pvpython)であり、この問題の影響を受けない
- (任意)上流 Kitware/ParaView への issue 報告(§7。gdb バックトレース・最小再現手順は取得済み)

### 1.3 In / Out

| 項目 | M2 | 備考 |
|---|---|---|
| `get_state(detail)` ツール+スニペット | **In** | §7.3。S-08 |
| `reset_session` ツール+スニペット | **In** | §7.4。S-09 |
| bridge_client のタイムアウト/切断文言を `get_state` 案内へ差し替え | **In** | M1_PLAN C-03 / C-06 の予告の履行。S-10 |
| integration CI(実 pvpython + standalone ブリッジ) | **In** | §11-2。REQ-I / REQ-CI |
| SMOKE.md + tests/smoke/run_smoke.py | **In** | §11-3。REQ-SM |
| README 改稿(セキュリティ注意・WSL 手順含む) | **In → 実施済み**(2026-07-19、本プラン策定と同時) | §13 M2 |
| パッケージングメタデータ残務(license 表記・URL・未使用依存) | **In**(小粒) | S-12。下記 1.4-2 |
| instructions 全面チューニング / 自動起動調査 / promptfoo 回帰評価 | Out → M3 | §13 |
| ブリッジ変更(理由を問わず) | Out | 1.1 |
| pvserver+マクロ登録セグフォの修正 | Out(不可能) | 1.2。ParaView 側の問題 |

### 1.4 本計画で見つけた既存成果物の調整点

M1_PLAN §1.3 と同様、計画時点で確定させておく不整合・残務:

1. **`get_state(detail="summary")` はスニペット不要**。DESIGN §7.3 は「定型スニペットで実装」とするが、B-17 により §6.5 と同一の state 要約が**全応答に既に付いている**。summary は `ping` を 1 往復送り、その応答の `state` を返すだけでよい(空コードの `exec` より軽く、副作用ゼロ)。スニペットは arrays / full にのみ用いる。「ブリッジは関知しない」という §7.3 の本旨と一致するため DESIGN.md の修正は不要。
2. **pyproject.toml のメタデータが上流の名残のまま**: `license = MIT` は LICENSE ファイル(BSD-3-Clause)と矛盾 / classifiers の License 行も MIT / `project.urls` が LLNL 上流を指す / `httpx` は依存に宣言されているがどこからも import されていない。S-12 で一括修正する。
3. **C-03(タイムアウト)/ C-06(切断)の案内文言**は M1 実装時の予告どおり(「M1 では get_state が無いため execute_python / get_screenshot を案内。M2 で get_state 文言に差し替え」— M1_PLAN §3.2)、get_state 追加と同時に差し替える。対応する unit テストの文言 assert も更新する。

## 2. 成果物

```
paraview_mcp/
  pyproject.toml                  # S-12: license / urls / 未使用依存の残務修正のみ
  src/paraview_mcp/
    snippets.py                   # + GET_STATE_ARRAYS / GET_STATE_FULL / RESET_PIPELINE
    server.py                     # + get_state / reset_session ツール(既存 3 ツールは変更なし)
    bridge_client.py              # C-03 / C-06 文言差し替えのみ
  tests/
    fakes/paraview/               # 偽 proxy に arrays / full スニペットが呼ぶ API 形状を追加
    unit/
      test_server_tools.py        # + get_state / reset_session ケース、文言 assert 更新
    integration/                  # 新設(実 pvpython が無い環境では自動 skip)
      conftest.py                 # pvpython 検出・standalone ブリッジ subprocess fixture
      test_e2e_protocol.py        # 生 TCP / NDJSON レベル
      test_e2e_tools.py           # MCP サーバー stdio 経由のフルチェーン
    smoke/
      run_smoke.py                # 実 GUI への半自動スモークドライバ
  docs/
    SMOKE.md                      # 手動スモーク手順書(M1_PLAN §5 の後継)
  .github/workflows/
    integration.yml               # 新設。unit.yml は変更しない
```

(README.md は本プラン策定と同時に改稿済みのため上記に含めない。ブリッジ・既存 unit テスト・unit CI は無変更)

## 3. 要件一覧

### 3.1 サーバー / スニペット(REQ-S、M1 からの続番)

| ID | 要件 | 参照 |
|---|---|---|
| S-08 | `get_state(detail: Literal["summary","arrays","full"] = "summary") -> structured`。summary は `ping` の応答の `state` をそのまま返す(1.4-1)。arrays は summary+各ソースの point/cell 配列(名前・成分数・レンジ)。full は arrays+各ソースの bounds・セル数・代表プロパティ。arrays / full はスニペットで実装し、`render: false`・`max_value_bytes: 4 MiB` で送る(サーバー往復を伴う `GetDataInformation` 等を使ってよいのはこのスニペットだけ — B-17 の禁止は毎応答 state の話。4 MiB はソース 50 件上限でも切詰めが起きない余裕値) | §7.3, §6.5 |
| S-09 | `reset_session(clear_pipeline=True, clear_namespace=True) -> text`。pipeline 削除はスニペット:「他ソースの入力として参照されていないソースを `Delete()` し、名前空間内の当該プロキシ参照も `del`」を残数が減らなくなるまで反復し、削除件数を返す。`ResetSession()` は使わない(§7.4 の理由)。namespace 初期化はブリッジ既存の `reset` op。両方 true の場合は pipeline → namespace の順で 2 リクエスト | §7.4, §5.2 |
| S-10 | bridge_client の C-03(タイムアウト)/ C-06(切断)ガイダンス文言を「`get_state` で確認せよ」へ差し替え。server の instructions にも get_state / reset_session の使い分けを 1 行ずつ追記(全面チューニングは M3) | §10 |
| S-11 | 新スニペットは共有名前空間を汚さない: `__` 接頭辞のローカル名のみを使い、実行後に名前を残さない(既存 GET_SCREENSHOT と同じ規約)。ユーザー定義の変数を上書き・削除しない(例外: reset_session はその目的上、削除したプロキシを指す名前空間内の参照と、`clear_namespace=true` 時の名前空間全体を掃除する) | snippets.py docstring |
| S-12 | パッケージング残務: `license` を BSD-3-Clause に(LICENSE と一致させる)、classifiers の License 行も修正、`project.urls` をフォーク先リポジトリへ、未使用の `httpx` を依存から削除 | 1.4-2 |

### 3.2 integration テスト(REQ-I)

| ID | 要件 | 参照 |
|---|---|---|
| I-01 | 実行環境検出: `PARAVIEW_MCP_PVPYTHON` 環境変数 > PATH 上の `pvpython` の順で探し、見つからなければ収集時に明示メッセージ付きで skip(unit だけ走らせたい開発者・CI を汚さない) | §11-2 |
| I-02 | fixture: 空きポートで `pvpython --force-offscreen-rendering bridge/paraview_mcp_bridge.py --standalone --port N` を subprocess 起動し、listening 行の出力を待ってから yield、テスト終了時に SIGTERM。基本は 1 セッション 1 プロセスで使い回し、名前空間の独立が要るテストは `reset` を前置する | §9.4 |
| I-03 | `test_e2e_protocol.py`(生 TCP / NDJSON): ping(バージョン・`session_type: builtin`)/ exec 末尾式評価 / 名前空間持続 / reset / **実 VTK エラー**の vtk_messages 捕捉 / 不正 JSON → protocol_error。実 paraview に対する初のプロトコル自動検証(unit の偽 paraview では担保できなかった層) | §11-2 |
| I-04 | `test_e2e_tools.py`(`mcp.ClientSession`(stdio)→ サーバー → bridge_client → 実ブリッジのフルチェーン): bridge_status / execute_python(Sphere 生成 → 点数・state 検証)/ get_screenshot が**有効な JPEG かつ期待サイズ**(ピクセル比較はしない)/ get_state summary・arrays・full の形状検証 / reset_session 後に sources が空 | §11-2 |
| I-05 | 担保範囲の限定を明文化: GUI 経路(タイマー駆動・Qt との相互作用)・実 GPU レンダリング・公式バイナリと conda 版の差異は integration では担保しない(手動スモークの担当)。テストファイル冒頭の docstring に記載 | §11-2 |

### 3.3 integration CI(REQ-CI)

| ID | 要件 | 参照 |
|---|---|---|
| CI-01 | `.github/workflows/integration.yml` を新設。トリガは unit と同じ push(main, dev)+ pull_request に加え `workflow_dispatch`(手動再実行)。**unit.yml は変更しない**(integration の不安定さが unit の信号を汚さない) | §11-2 |
| CI-02 | ジョブ構成: ubuntu-latest。`mamba-org/setup-micromamba` で conda-forge から `paraview` を導入(バージョンは実装時に入手可能な 6.x を確認してピン留め。6.x が無ければ最新でスパイクし判断)し、その `pvpython` を `PARAVIEW_MCP_PVPYTHON` で I-01 に渡す。テスト実行側は unit と同じ uv 管理 venv(pvpython の Python とサーバーの Python が別物であることは設計どおり — §1) | §11-2 |
| CI-03 | 初期は allow-failure(`continue-on-error: true`)で導入し、**解除基準を明文化する**: 10 run 連続 green または 2 週間安定のいずれか早い方で外す | §11-2 |
| CI-04 | micromamba 環境をキャッシュして 2 回目以降を短縮。ジョブ全体に `timeout-minutes`(目安 20 分)を設定し、ハング時に走り続ける事態を防ぐ | — |

### 3.4 手動スモーク(REQ-SM)

| ID | 要件 | 参照 |
|---|---|---|
| SM-01 | `docs/SMOKE.md`: M1_PLAN §5 の表を昇格・拡張した番号付きチェックリスト(手順・期待結果・記入欄)。シナリオ: ① builtin(マクロ登録起動)で全 5 ツール ② タイムアウトと直後の回復 ③ ParaView 終了時のガイダンス(サーバー生存)④ ブリッジを載せた view を閉じた際の診断(§10「接続は成立するが ping 無応答」)⑤ pvserver 接続(**Python Shell 貼り付け起動を正式手順として記載** — 1.2)⑥ get_state / reset_session(M2 新ツール)。末尾に実施日・ParaView 版数・環境の記録表 | §11-3 |
| SM-02 | `tests/smoke/run_smoke.py`: `mcp.ClientSession`(stdio)でサーバーを子プロセス起動し、SMOKE.md の操作列(bridge_status → execute_python → get_screenshot のファイル保存 → 短 timeout_s → get_state → reset_session)を順に流して PASS / FAIL と「要目視」項目を印字する。人間は GUI の目視と ParaView 側の操作のみを担当(M1 §5 の受け入れで用いた直接クライアント方式の成果物化) | §11-3 |
| SM-03 | M2 完了判定としてのスモーク実施: ParaView 6.1.1 実機(WSL2)で SMOKE.md 全シナリオを実施し、結果を SMOKE.md に記録する | §11-3 |

## 4. テスト設計(unit 追加分)

integration の設計は §3.2 が正。unit の追加は `test_server_tools.py` が中心で、偽 bridge_client 差し替え方式(M1_PLAN §4.1 シーム 3)は既存のまま:

- `get_state("summary")` → 送信 op が `ping` で、応答の `state` がそのまま structured で返る(S-08)
- `get_state("arrays" / "full")` → スニペット exec が送られ、`max_value_bytes = 4 MiB`・`render: false`(S-08)
- `detail` に不正値 → 例外ではなく明確なツールエラー(S-08)
- `reset_session(True, True)` → pipeline スニペット → `reset` op の順で 2 呼び出し(S-09)
- `reset_session(False, True)` / `(True, False)` → 対応する片方のみ送られる(S-09)
- 接続不可時の get_state / reset_session → ガイダンス付きツール結果で例外にしない(C-01 の踏襲)
- C-03 / C-06 の新文言に合わせて既存 assert を更新(S-10)
- スニペット文字列の静的検査: `__` 接頭辞以外への代入が無いこと(S-11。既存 GET_SCREENSHOT も対象に含める)

`tests/fakes/paraview/` には arrays / full スニペットが呼ぶ API の形状(`GetDataInformation` 系)を追加する。unit はあくまで API 形状の模倣であり、実挙動の担保は I-03 / I-04(実 pvpython)が引き継ぐ。

## 5. M2 受け入れ基準

実施したら結果を記入する(M1_PLAN §5 と同じ運用)。

| # | 項目 | 期待結果 | 結果 |
|---|---|---|---|
| 1 | unit CI(ローカル、`uv run pytest tests/unit`) | 既存 76 件+追加分がすべて green(ruff clean) | PASS(2026-07-21)。96 件 green。GitHub Actions 上(Python 3.10〜3.12 マトリクス)での実行は push 後に別途確認要 |
| 2 | integration をローカルの実 pvpython で実行 | 全ケース PASS | PASS(2026-07-21)。ParaView 6.1.1(WSL2)、pvpython standalone、12 件 green。実行中に S-03/S-05 の `structuredContent` 欠陥を発見・修正(上記 Status 参照) |
| 3 | integration CI(GitHub Actions) | ジョブ green(allow-failure 運用中でもジョブ自体の green を確認) | 未実施(ワークフロー定義のみ作成。push してのトリガー確認が必要) |
| 4 | SMOKE.md 全シナリオ(ParaView 6.1.1 実機 / WSL2 GUI) | 全項目 PASS・記録済み(pvserver は Shell 貼り付け手順) | 未実施(GUI 操作が必要)。`run_smoke.py` 自体は standalone pvpython 相手に動作確認済み(basic/timeout/disconnected 全 PASS)で、GUI 実施時に流すスクリプトとしての健全性は担保済み |
| 5 | マクロ差し替え不要の確認 | M1 時点のブリッジのまま M2 サーバーで全ツールが動く(ブリッジに diff が無いことの確認+実機スモークで担保) | PASS(2026-07-21、部分)。`git diff` で bridge/paraview_mcp_bridge.py に無変更を確認済み。実機 GUI での動作確認は #4 未実施につき持ち越し |

## 6. 実装順序

1. S-08〜S-11(スニペット+ツール+文言差し替え)+ unit テスト追加 → unit green
2. `tests/integration`(I-01〜I-05)をローカルの実 pvpython で green 化
3. `integration.yml`(CI-01〜CI-04)→ Actions 上で green を確認
4. SMOKE.md + run_smoke.py(SM-01〜SM-02)→ 実機スモーク実施・記録(SM-03)
5. S-12(パッケージングメタデータ残務)

(README.md は本プラン策定と同時に改稿済み)

## 7. 任意項目(M2 のスコープ外だが M2 期間中に判断する)

- **上流への issue 報告**: pvserver+マクロ登録セグフォ(M1_PLAN §5 #8)。gdb バックトレース・M0 / M1 両実装での再現・Shell 貼り付けでは発生しないという切り分けまで揃っており、報告コストは低い。報告先は ParaView の GitLab(gitlab.kitware.com/paraview/paraview)
- LLNL 上流への還元判断は M3(§13)
