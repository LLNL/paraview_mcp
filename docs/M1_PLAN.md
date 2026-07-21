# M1 要件設計・テスト設計

- Status: **完了**(2026-07-19)— §6 の実装項目 1〜5 すべて完了。§5 受け入れ: 必須 #1〜7 PASS、#8(任意)は ParaView 側起因として切り分け済みの FAIL(回避策あり)
- 対応マイルストーン: [DESIGN.md](DESIGN.md) §13 M1(MVP)
- 前提: M0 完了(4項目すべて PASS、[M0_SPIKE.md](M0_SPIKE.md))
- 本書の位置づけ: DESIGN.md が仕様の正。本書は M1 で実装する範囲の確定・要件の実装単位への分解・テスト設計のみを扱い、仕様の詳細は DESIGN.md の節番号で参照する。

## 1. スコープ

### 1.1 方針: ブリッジは M1 で完成させ、以後凍結する

ブリッジの再配布は「ユーザーがマクロを差し替えて再実行する」という手動操作を要する、このシステムで最も高コストな変更である。よって M1 でワイヤプロトコル v1 の全機能(ping / exec / reset、認証、フレーミング、部分送信、再入ガード、vtk_messages 捕捉、state 要約、embedded / standalone 両モード)を実装し、v1 の期間中はブリッジに手を入れない。以後の機能追加はすべてサーバー側の定型スニペット(§3 原則 1)で行う。

この方針により、DESIGN.md §13 の旧記載で M2 に置かれていた「VTK メッセージ捕捉」は M1 に前倒しする(M0 スパイクで実装・実機検証済みのため追加コストはほぼ無い)。同様に、サーバー側のタイムアウト・遅延応答破棄・再接続も M1 に含める。タイムアウト処理の無い MCP サーバーは `execute_python` が一度詰まると MCP クライアント側のツール呼び出しが無期限にハングするため、MVP として成立しないからである。§13 は本書に合わせて改訂済み。

### 1.2 In / Out

| 項目 | M1 | 備考 |
|---|---|---|
| ブリッジ(プロトコル v1 全機能、embedded / standalone) | **In** | 以後凍結 |
| サーバー: `execute_python` / `get_screenshot` / `bridge_status` | **In** | §7.1 / §7.2 / §7.5 |
| bridge_client(直列化・タイムアウト・遅延応答破棄・再接続) | **In** | §4.2 / §5.4 |
| FastMCP instructions(初版) | **In** | §7.6。チューニングは M3 |
| パッケージング(console script `paraview-mcp`)+ unit テスト + unit CI | **In** | §9.1 / §11-1 |
| `get_state(detail)` / `reset_session` ツール | Out → M2 | サーバー側スニペットのみで実現可(ブリッジ変更なし) |
| integration CI(実 pvpython)/ SMOKE.md / run_smoke.py / README 改稿 | Out → M2 | §11-2 / §11-3 |
| instructions チューニング / 自動起動 | Out → M3 | §13 |

### 1.3 本設計で見つけた DESIGN.md の不整合(修正済み)

1. **§5.2 `reset` の `pipeline` フィールドを削除**。§7.4 は「パイプライン全削除はサーバー側の定型スニペットで行う」と定めており、ブリッジ側にも削除機能を持たせるのは二重実装かつ §3 原則 1(ブリッジはダムな実行器)に反する。`reset` は名前空間の初期化のみとする。
2. **§5.2 `exec` に `max_value_bytes`(既定 256 KiB)を追加**。§6.2 の「`value` は 256 KiB で切詰め」と §7.2 の「スクリーンショットを base64 で `value` として返す」が矛盾していた(数 MB の base64 が必ず切詰められ壊れる)。サーバー内部(定型スニペット)専用のフィールドとして上限を可変にする。
3. **§5.3 応答例に `stderr` を追加**。§6.3 で捕捉すると定めているのに応答スキーマ例に欠けていた。
4. **§13 M1 / M2 の再配分**(上記 1.1)。

## 2. 成果物

```
paraview_mcp/
  pyproject.toml                  # packaging 再有効化(hatchling)、console script、dev 依存整理
  bridge/
    paraview_mcp_bridge.py        # 単一ファイル・stdlib のみ・Python 3.9 構文まで
  src/paraview_mcp/
    __init__.py                   # バージョン定数のみ。paraview を import しない
    server.py                     # FastMCP アプリ・ツール 3 種・instructions・main()
    bridge_client.py              # 接続・フレーミング・直列化・タイムアウト・遅延応答破棄・再接続
    snippets.py                   # get_screenshot 用の定型コード(文字列定数)
  tests/
    fakes/
      paraview/                   # import 可能な偽 paraview / vtk(unit と standalone サブプロセステストで共用)
    unit/
      conftest.py                 # 偽モジュール注入、ブリッジ import ヘルパ、偽ブリッジサーバー
      test_bridge_import.py
      test_bridge_exec.py
      test_bridge_protocol.py
      test_bridge_socketloop.py
      test_bridge_client.py
      test_server_tools.py
      test_standalone_subprocess.py
  .github/workflows/unit.yml      # uv sync → pytest tests/unit
```

付随作業(パッケージング整理): `[tool.uv] package = false` を外し hatchling でビルド、dev 依存を `ruff` + `pytest` + `pytest-asyncio` に整理(black / flake8 / mypy / pre-commit は上流の名残であり削除)。ブリッジのみ Python 3.9 構文縛り(§4.1)である点はレビューで担保し、ツールでの強制は行わない。

## 3. 要件一覧

各行は実装とテストの追跡単位。詳細仕様は参照節が正。

### 3.1 ブリッジ(REQ-B)

| ID | 要件 | 参照 |
|---|---|---|
| B-01 | 単一ファイル・標準ライブラリのみ・Python 3.9 構文。**モジュール import 時に paraview / vtk を import せず、bind もスレッド生成もしない**(全て遅延) | §4.1 |
| B-02 | embedded: `start()` は RenderView を探し `GetInteractor()` に 50ms 繰返しタイマー+`TimerEvent` オブザーバを登録。RenderView 無しは明確なエラー | §4.1 |
| B-03 | `start()` 冪等: 自身の旧 listener/タイマーを先に破棄。bind 失敗時は当該ポートへ ping を打ち、ブリッジ応答なら「起動済み」正常終了、無応答なら `PARAVIEW_MCP_PORT` 変更案内のエラー | §4.1 |
| B-04 | 起動ログ: bind 成功時に listening 行、最初の tick で `bridge active` 行 | §4.1 |
| B-05 | バインド 127.0.0.1 固定。ポートは引数 > `PARAVIEW_MCP_PORT` > 9911 | §4.1 |
| B-06 | 1 tick = `select(timeout=0)` → accept / 受信 / 完全行の逐次実行 / 送信バッファ排出。バッファはコネクション単位、切断時は当該コネクションごと破棄 | §4.1 |
| B-07 | 大きな応答は部分書き込みで複数 tick にまたがって送信(EWOULDBLOCK 対応)。応答送信完了直後のクライアント切断でクラッシュしない(M0 で踏んだ `_on_writable` バグの回帰要件) | §4.1, M0_SPIKE 特記事項 |
| B-08 | 再入ガード: `_on_tick` 先頭で実行中フラグを検査し再入時は即 return | §4.1 |
| B-09 | standalone: `pvpython paraview_mcp_bridge.py --standalone [--port N]`。ブロッキング select ループで同一ハンドラを駆動。タイマー・GUI 非依存 | §4.1, §9.4 |
| B-10 | `ping`: `value` に `{bridge_version, paraview_version, python_version, session_type("builtin"/"client-server"), server(接続先 or null)}` を返す | §5.2, §7.5 |
| B-11 | `exec`: 単一の永続名前空間で実行(初回に `from paraview.simple import *` 等を遅延初期化)。末尾式の評価値を `value` に(IPython 規約) | §6.1, §6.2 |
| B-12 | `value` シリアライズ: `json.dumps` 成功なら `value_is_json: true`、失敗なら `repr` で false。切詰めはリクエストの `max_value_bytes`(既定 256 KiB)、切詰め時は `…(truncated)` 付記 | §6.2 |
| B-13 | `stdout` / `stderr` を実行中のみ redirect で捕捉(各 64 KiB 切詰め)。`vtk_messages` を `vtkStringOutputWindow` 差替えで捕捉し、**例外時も finally で必ず元へ復元** | §6.3 |
| B-14 | 例外は `BaseException` 単位で `exec_error` 化(`SystemExit` / `KeyboardInterrupt` 含む)。traceback は末尾 40 行 | §6.3, §5.3 |
| B-15 | `render: true`(既定)なら実行成功後に `Render()` を try/except で呼ぶ | §6.4 |
| B-16 | `reset`: 名前空間を初期状態に戻す(パイプラインには触れない) | §5.2, §7.4 |
| B-17 | state 要約を**全応答**(ping / exec / reset、成功・失敗問わず)に付与。生成失敗時は `state: null` で応答は返す。`visible` は Representations 走査(`GetRepresentation()` 禁止)。50 件超は先頭 50 件+`truncated: true`。サーバー往復呼び出し(`GetDataInformation` 等)禁止 | §5.3, §6.5 |
| B-18 | 認証: ブリッジ側 `PARAVIEW_MCP_TOKEN` 設定時のみ全リクエストで照合、不一致は `auth_error`。トークン値をログに書かない。未設定時は `token` フィールドを無視 | §5.2, §8 |
| B-19 | プロトコルエラー処理: JSON 不正 → `protocol_error`(`id: null`、接続維持)/ 未知 op・`v` 不一致 → `protocol_error` / 1 行 64 MiB 超 → `protocol_error` 応答後に接続を閉じる | §5.1, §5.2 |
| B-20 | スレッドを一切作らない | §3 原則 2 |

### 3.2 bridge_client(REQ-C)

| ID | 要件 | 参照 |
|---|---|---|
| C-01 | 遅延接続・永続コネクション。接続失敗はリトライ 2 回(計 ~2 秒)後、§10 の「マクロを実行せよ」文言のエラー。**例外でサーバーを落とさない** | §4.2, §10 |
| C-02 | `asyncio.Lock` で全ブリッジ呼び出しを直列化(同時 in-flight は常に 1) | §4.2 |
| C-03 | リクエスト毎タイムアウト。超過時は「実行は継続中の可能性。GUI フリーズ中なら処理中。次のリクエストは前の実行完了まで待たされる」文言のエラー | §4.2, §5.4, §10 |
| C-04 | 応答の破棄規則: **現在待機中の `id` 以外の行はすべて黙って捨てる**(遅延応答・ノイズ行対応はこの 1 規則のみ) | §5.4 |
| C-05 | NDJSON 受信は行分断・結合に頑健。1 行 64 MiB 上限 | §5.1 |
| C-06 | 切断後の次のツール呼び出しで自動再接続。**実行待機中の切断**は「結果は失われたが実行は完了している可能性がある」文言のエラー(M1 では `get_state` が無いため、確認手段として `execute_python` / `get_screenshot` を案内。M2 で `get_state` 文言に差し替え) | §4.2, §10 |
| C-07 | リクエスト組立: `v: 1`、一意な `id`、環境変数からの `token` 付与、op 別フィールド(`max_value_bytes` 含む) | §5.2 |
| C-08 | `auth_error` / `protocol_error` / バージョン不一致は設定確認手順の文言に変換 | §10 |

### 3.3 サーバー(REQ-S)

| ID | 要件 | 参照 |
|---|---|---|
| S-01 | FastMCP + stdio。**stdout には MCP フレーム以外を一切書かない**。ログは stderr、`PARAVIEW_MCP_LOG` 設定時はファイルにも | §4.2 |
| S-02 | 設定は環境変数: `PARAVIEW_MCP_HOST` / `PARAVIEW_MCP_PORT` / `PARAVIEW_MCP_TOKEN` / `PARAVIEW_MCP_LOG` | §4.2 |
| S-03 | `execute_python(code, timeout_s=120, render=True)` → structured `{ok, value, value_is_json, stdout, stderr, vtk_messages, error, state, duration_ms}`。エラー時も traceback / vtk_messages を LLM にそのまま提示 | §7.1, §10 |
| S-04 | `get_screenshot(max_width=1280, quality=80)`: 定型スニペット(`SaveScreenshot` → 一時ファイル → base64 → 削除。RenderView 無ければ作る)を `max_value_bytes=32 MiB` で送信。サーバー側で Pillow により `max_width` へ縮小・JPEG 再圧縮し MCP Image + テキスト(view 種別・元サイズ・バイト数)を返す | §7.2 |
| S-05 | `bridge_status()`: ping 結果+サーバー側設定(host/port)。接続不可でも例外ではなくガイダンス付きツール結果 | §7.5, §10 |
| S-06 | instructions 初版: 小さなステップ実行 / スクリーンショットでの確認 / 長時間処理前のユーザー告知と `timeout_s` 引き上げ / `Delete(obj); del obj` / `vtk_messages` を読む / パスは ParaView 側 | §7.6 |
| S-07 | console script `paraview-mcp = paraview_mcp.server:main` | §9.1 |

## 4. テスト設計

### 4.1 方針: 3 つのテストシーム

unit テストは **ParaView 無しの素の CPython** で全て走る(§11-1)。これを成立させる構造上の取り決めが以下の 3 つで、実装はこれをテスト可能性要件として守ること。

1. **遅延 import(B-01)**: ブリッジは paraview / vtk を関数内でのみ import する。テストは `sys.modules` に偽 `paraview` / `paraview.simple` / `paraview.servermanager` / `vtk` を注入してからブリッジを import し、exec エンジンと state 要約を偽物相手に検証する。偽 vtk は `vtkStringOutputWindow` / `vtkOutputWindow.Get/SetInstance` と「現在のインスタンスへメッセージを送る」テスト用ヘルパを持つ。
2. **listener 起動とイベント駆動の分離**: `start()` を「listener 準備(`_start_listener`)」と「タイマー装着」に分け、unit テストは実 TCP ソケット(port=0 の一時ポート)+ **`_poll_once()` の手動呼び出し**で tick を決定的に駆動する。タイマー・GUI は一切不要になり、部分送信・切断タイミングのテストが再現可能になる。standalone モードは同じ `_poll_once` をブロッキングループで回すだけの構成とする。
3. **偽ブリッジ**: bridge_client / サーバーツールのテストは、テスト内 asyncio TCP サーバー(NDJSON を話し、正常応答・遅延・切断・断片化・ノイズ行を台本どおり演じる)を相手にする(§11-1 の「偽ブリッジ」)。

### 4.2 テストケース一覧

各ケース末尾の括弧は対応要件。

**test_bridge_import.py**
- 偽モジュールすら無い素の環境で import が成功する(B-01)
- import 直後、`sys.modules` に paraview / vtk が入っておらず、listener も無い(B-01)

**test_bridge_exec.py**(偽 paraview/vtk 注入、ソケット無しでハンドラ直接呼び出し)
- 末尾が式 → `value` 返却、JSON 可能値は `value_is_json: true`(B-11, B-12)
- JSON 不可能値(偽プロキシ)→ `repr` + `value_is_json: false`(B-12)
- 末尾が文 / 空コード → `value: null`(B-11)
- SyntaxError → `exec_error`(B-14)
- 名前空間が exec をまたいで持続する / `reset` 後は消える / `__name__` が設定される(B-11, B-16)
- `stdout` / `stderr` の捕捉と各 64 KiB 切詰め(B-13)
- `value` の既定 256 KiB 切詰め+`…(truncated)` / `max_value_bytes` 指定で大きな値が通る(B-12)
- traceback が末尾 40 行に切詰められる(B-14)
- `raise SystemExit` / `KeyboardInterrupt` → `exec_error` になりプロセスは生きている(B-14)
- 実行中に偽 vtkError 発生 → `vtk_messages` に載る(B-13)
- 実行が例外終了しても vtkOutputWindow のインスタンスが復元されている(B-13)
- `render=true` で `Render()` が呼ばれ、`false` で呼ばれず、`Render()` の例外は結果を汚さない(B-15)
- state が全応答に付く / `GetSources()` が例外を投げても応答は返り `state: null`(B-17)
- `visible` 判定で `GetRepresentation()` が呼ばれない(偽 proxy で検出)(B-17)
- 51 ソース → 50 件+`truncated: true`(B-17)

**test_bridge_protocol.py**(行の直接投入)
- 正常リクエスト → `v` / `id` エコー、`status: ok`(B-06)
- JSON 不正行 → `protocol_error`・`id: null`・接続維持(次の正常行が処理される)(B-19)
- 未知 op / `v: 2` → `protocol_error`(B-19)
- トークン: ブリッジ側設定時、一致 → ok / 不一致・欠落 → `auth_error` / 未設定時は無視(B-18)
- `auth_error` 応答にトークン値が含まれない(B-18)
- `ping` の `value` に B-10 の全フィールド(B-10)
- `reset` → ok、名前空間初期化(B-16)

**test_bridge_socketloop.py**(実 TCP + `_poll_once` 手動駆動)
- accept → リクエスト → 応答の往復(B-06)
- 1 リクエストが複数セグメントで届く / 複数リクエストが 1 セグメントで届く → 全応答・順序保存(B-06)
- 複数コネクション同時接続(B-06)
- 8 MiB 応答が複数 tick にまたがって完全送信される(B-07)
- クライアントが応答受信直後に即 close してもエラーが出ずコネクション破棄される(**M0 `_on_writable` バグの回帰テスト**)(B-07)
- 送信途中の切断 → 当該コネクションのみ破棄、他コネクションへ影響なし(B-06, B-07)
- 64 MiB 超の行 → `protocol_error` 応答+接続クローズ(B-19)
- 実行中フラグが立った状態の `_on_tick` は `_poll_once` を呼ばずに戻る(B-08)
- `_start_listener` 2 回目が旧 listener を閉じて成功する(B-03)
- ポート占有相手が偽ブリッジ(ping 応答)→「起動済み」正常終了 / ただの占有 → 案内付きエラー(B-03)

**test_bridge_client.py**(asyncio、偽ブリッジ)
- 正常往復、`v` / `id` / `token` / `max_value_bytes` がリクエストに載る(C-05, C-07)
- 応答が 1 バイトずつ届いても組み立てられる(C-05)
- タイムアウト → C-03 文言のエラー。**その後に届く遅延応答は破棄され、次のリクエストは正常に通る**(C-03, C-04)
- 待機中 `id` と異なる行・ゴミ行は黙って捨てられる(C-04)
- 接続拒否 → リトライ 2 回 → C-01 文言(C-01)
- 偽ブリッジ再起動後、次の呼び出しで自動再接続(C-06)
- 待機中の切断 → C-06 文言(C-06)
- 並列 5 呼び出し → 偽ブリッジの観測上 in-flight が常に 1(C-02)
- `auth_error` 応答 → 設定確認文言(C-08)

**test_server_tools.py**(bridge_client を偽物に差し替え)
- `execute_python`: 引数伝搬(code / render / timeout_s)、応答 → structured マッピング、エラー時に traceback / vtk_messages が結果に含まれる(S-03)
- `get_screenshot`: 既知 PNG の base64 を返す偽ブリッジ → JPEG Image、幅 ≤ max_width、テキストに元サイズ・種別。リクエストに `max_value_bytes = 32 MiB`(S-04)
- `bridge_status`: 成功時マッピング / 接続不可 → 例外ではなくガイダンス(S-05)
- ログを書いても stdout に何も出ない(capfd)(S-01)
- instructions が空でない(S-06)

**test_standalone_subprocess.py**(偽 paraview を PYTHONPATH に載せてサブプロセス起動)
- `python bridge/paraview_mcp_bridge.py --standalone --port <空きポート>` → 実 TCP で ping / exec 往復 → SIGTERM で終了(B-09)
- これが「ブリッジ全体を 1 プロセスとして通す」unit 側の最上位テスト。実 pvpython 版は M2 integration が引き継ぐ

### 4.3 unit で担保しないもの(M1 の手動受け入れ / M2 に委譲)

- 実 `paraview.simple` に対するスニペット・state 要約の実地動作(偽物は API 形状のみ模す)
- タイマー駆動・Qt との相互作用・実レンダリング・pvserver 接続(M0 で実証済み、継続的担保は M2 integration + 手動スモーク)
- スクリーンショットが「見た目として正しい」こと(手動確認のみ。ピクセル比較はしない — §11-2)

## 5. M1 受け入れ基準(手動、WSL2 + ParaView 6.1.1)

SMOKE.md(M2 成果物)の前身。実施したら結果を下表に記入する。

| # | 手順 | 期待結果 | 結果 |
|---|---|---|---|
| 1 | ParaView 起動 → マクロ登録 → 実行 | listening 行+`bridge active` 行 | PASS (2026-07-19)。当初 `__name__ == "__main__"` ガードで無反応 → ParaView のマクロローダーは `__main__` を設定しないと判明、`PARAVIEW_MCP_BRIDGE_TEST_NO_AUTOSTART` 環境変数方式に修正して解消(bridge/paraview_mcp_bridge.py、tests/unit/conftest.py) |
| 2 | `uv run paraview-mcp` を MCP クライアント(MCP Inspector 等)に接続し `bridge_status` | ParaView / ブリッジ版数・session_type: builtin | PASS (2026-07-19)。MCP Inspector はプロキシ側で `SSE connection not established` を返す既知の不具合が発生したため、直接 `mcp.ClientSession`(stdio)で検証。`bridge_version: 1.0.0`, `paraview_version: 6.1.1`, `session_type: builtin` を確認 |
| 3 | `execute_python`: Sphere 生成 → Show → 点数を末尾式で | `value` に点数、GUI に球、state に Sphere1 | PASS (2026-07-19)。`value: 50`、`state.sources` に `Sphere1`(visible/active true) |
| 4 | `get_screenshot` | GUI の見た目と一致する JPEG | PASS (2026-07-19)。1280x400 JPEG を保存し目視確認、球が正しく描画 |
| 5 | `execute_python`: `import time; time.sleep(5)` を `timeout_s=2` で | タイムアウト文言。直後の呼び出しは正常(遅延応答が破棄される) | PASS (2026-07-19)。timeout_s=2 で `kind: connection_error` のタイムアウト文言を確認。直後の呼び出しは `value: 2`、`state` に既存の Sphere1 を保持したまま正常応答 |
| 6 | ParaView を終了して `bridge_status` | 「マクロを実行せよ」ガイダンス(サーバーは生存) | PASS (2026-07-19)。`connected: false`、`guidance` に "run the paraview_mcp_bridge macro to start it" を含む応答。MCP サーバー(paraview-mcp プロセス)はクラッシュせず正常終了 |
| 7 | `pvpython --force-offscreen-rendering bridge/paraview_mcp_bridge.py --standalone` に対し 2〜4 相当 | GUI 無しで同一の結果 | PASS (2026-07-19)。port 9912 で起動、`bridge_status`(connected/builtin)・`execute_python`(value: 50, Sphere1)・`get_screenshot`(400x400 JPEG)いずれも embedded 版と同一結果 |
| 8 | (任意)pvserver 接続下で 2〜4 | builtin と同一挙動、session_type: client-server | **FAIL(切り分け済み、M1 のコードが原因ではない)** (2026-07-19)。pvserver 接続下でマクロとして登録・実行すると、ブリッジは `listening on 127.0.0.1:9911` まで正常にログ出力した直後に ParaView GUI が `Segmentation fault` で強制終了(gdb 実子プロセス追跡で確認: メインスレッド上、`main→QCoreApplication::exec→Qt タイマー→QVTKInteractorInternal::TimerEvent→vtkPythonCommand::Execute→_PyEval_EvalFrameDefault` 内でクラッシュ。GIL 違反やバックグラウンドスレッドからの呼び出しではなく、想定通りメインスレッド上の正規経路)。同時刻の pvserver 側ログの `vtkSocketCommunicator: Could not receive tag` はクライアント側クラッシュによるソケット異常終了の症状。<br>切り分け実験: **未変更の M0 スパイク(`bridge/spike/m0_bridge_spike.py`)と M1 本実装の両方**で、①Python Shell に直接貼り付けて実行 → pvserver 接続下でも問題なし、②マクロとして登録して実行 → 両方とも同一条件でクラッシュ再現。マクロ登録の有無だけが変数で、M0/M1 のコード差分は無関係と確定。ParaView 自身の「マクロ実行機構」と「client-server 接続時のタイマー/オブザーバー登録」の相互作用に起因する ParaView 側の問題と推定される(M0_SPIKE.md のシナリオ②は Shell 貼り付けのみを検証しており、マクロ経由は未検証だった=検証漏れ)。M1 のブリッジコード自体に修正すべき欠陥は見つかっていない。回避策: pvserver 接続時はマクロ登録ではなく Python Shell へ直接貼り付けて実行する。DESIGN.md 200 行目により pvserver 接続の継続的担保は M2 integration + 手動スモークの守備範囲であり M1 必須項目(#1〜7)の合否には影響しない |

## 6. 実装順序

1. ブリッジ本体(M0 スパイクを土台に B-01〜B-20 へ拡張)+ ブリッジ系 unit テスト
2. `bridge_client` + 偽ブリッジ + クライアント系 unit テスト
3. `snippets.py` + `server.py`(ツール 3 種・instructions)+ サーバー系 unit テスト
4. パッケージング(hatchling・console script・dev 依存整理)+ unit CI
5. 受け入れ基準 §5 の手動実施・記録

スパイク(`bridge/spike/`)は M1 完了時に削除する(役目を終えるため。履歴には残る)。→ **削除済み(2026-07-19)**。§5 #8 の切り分け実験が参照した `bridge/spike/m0_bridge_spike.py` が再び必要になった場合は git 履歴から取得する。
