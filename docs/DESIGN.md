# ParaView MCP 再設計仕様書(案A: GUI 内ブリッジ + コード文字列実行)

- Status: Draft v1.1(2026-07-19、外部レビュー指摘 18 件の検証結果を反映)
- 対象 ParaView: 6.1.1 を主対象(ブリッジは 5.11+ / Python 3.9+ で動作することを目標とし、特定バージョンに依存しない)
- ライセンス: BSD-3-Clause(LLNL 上流のフォークであるため、LICENSE / NOTICE を維持する)

---

## 1. 背景と目的

人間が ParaView GUI を見ながら(必要なら自分でも操作しながら)、AI に自然言語で可視化操作を指示できるようにする。

旧実装(LLNL 上流)は「MCP サーバーが pvserver の第2クライアントとして接続し、GUI(第1クライアント)へコラボレーション機能で同期する」構成だったが、この機能は非保守・非推奨であり、GUI への反映が不安定。さらに MCP サーバー側 Python に `paraview` パッケージが必要なため、バージョン一致・依存関係の問題が深刻だった。

本設計では接続方式を根本から変更する:

- **ブリッジ**(単一ファイル・標準ライブラリのみ)を ParaView GUI の組み込み Python 内で起動する。
- **MCP サーバー**(通常の Python 環境、`paraview` 非依存)は、実行したい **Python コードを文字列として** ブリッジへ JSON で送る。
- ブリッジは GUI の **メインスレッド**で `paraview.simple` としてコードを実行し、結果を JSON で返す。

GUI が pvserver に接続している場合も、ブリッジは GUI が張っている既存セッションを使うだけなので、pvserver 側の変更・`--multi-clients` は不要。ビルトイン(GUI 単体)/client-server のどちらでも同一コードで動く。

## 2. スコープ / 非スコープ

### スコープ(v1)

- コード文字列実行(`execute_python`)を核とした最小ツールセット
- スクリーンショット取得(バイナリはソケット経由 base64 転送。ファイルシステム共有を前提にしない)
- パイプライン状態の要約取得
- 接続断・タイムアウトへの堅牢な対応
- pvpython 単体でブリッジを動かす standalone モード(CI・ヘッドレス運用兼用)

### 非スコープ(明示)

- **コードの静的検証・安全性検査**。ブリッジ/サーバーは検証を行わない。将来、サーバー側の送信前フック(§14)として**別プロジェクト**を差し込めるようにインターフェースだけ確保する。
- 構造化コマンド API(操作ごとのメソッド定義)。作らない。
- 実行中コードのキャンセル(VTK 処理は安全に中断できない)
- 複数 MCP クライアントの同時接続、リモートマルチユーザー、認証基盤
- 旧実装(35 ツール)との互換性

## 3. 全体アーキテクチャ

```
┌──────────────┐  stdio (MCP)   ┌─────────────────────┐  TCP 127.0.0.1 (NDJSON)  ┌───────────────────────────────┐
│ Claude       │◄──────────────►│ paraview-mcp server │◄────────────────────────►│ ParaView GUI プロセス          │
│ (Desktop/Code)│               │ ・純 Python          │                          │ ┌───────────────────────────┐ │
└──────────────┘                │ ・mcp SDK + Pillow   │                          │ │ bridge (stdlib のみ)       │ │
                                │ ・paraview 非依存    │                          │ │ selectors + タイマー駆動   │ │
                                └─────────────────────┘                          │ │ → メインスレッドで exec    │ │
                                                                                 │ └───────────────────────────┘ │
                                                                                 │  paraview.simple / 既存セッション│
                                                                                 └───────────────┬───────────────┘
                                                                                        (任意) TCP │ 通常の client-server
                                                                                                 ▼
                                                                                             pvserver
```

原則:

1. **ブリッジは「ダムな実行器」**。プロトコルの操作は `ping` / `exec` / `reset` の 3 つだけ。スクリーンショット取得や状態要約などの「ツール」は、すべて**サーバー側が保持する定型スニペット(canned code)を `exec` で送る**ことで実現する。ブリッジに機能を足すために GUI を再起動する事態を構造的に避ける。
2. **スレッドを作らない**。ブリッジはノンブロッキングソケット(`selectors`)を GUI メインスレッドのタイマーコールバックからポーリングする。受信・実行・送信のすべてがメインスレッドで直列に行われるため、スレッド安全性の問題が発生しない。
3. **結果は値で返す**(ファイルパスの受け渡しをしない)。スクリーンショットも base64 でソケットを通す。これにより Windows(Claude Desktop)+ WSL(ParaView)のようにファイルシステムが分かれる構成でも動く。

## 4. コンポーネント仕様

### 4.1 ブリッジ `bridge/paraview_mcp_bridge.py`

- **単一ファイル・標準ライブラリのみ**(`socket`, `selectors`, `json`, `ast`, `io`, `contextlib`, `traceback`, `base64`, `tempfile`, `os`, `sys`, `time`)。Python 3.9 構文まで。
- 2 つの動作モード:

| モード | 起動方法 | イベントループ |
|---|---|---|
| **embedded**(通常) | ParaView GUI のマクロ / Python Shell から `start()` | RenderView のインタラクタに `CreateRepeatingTimer`(既定 50ms)+ `TimerEvent` オブザーバを登録し、コールバック内で 1 tick 処理 |
| **standalone**(CI・ヘッドレス) | `pvpython paraview_mcp_bridge.py --standalone [--port N]` | 自前の `while True: select(...)` ブロッキングループ(メインスレッド = ループなのでタイマー不要) |

- embedded モードのタイマー登録:

```python
from paraview import simple
view = _find_render_view()          # GetActiveView() が RenderView でなければ views から探す
iren = view.GetInteractor()         # vtkSMRenderViewProxy.GetInteractor()
timer_id = iren.CreateRepeatingTimer(50)
iren.AddObserver('TimerEvent', _on_tick)
```

  - RenderView が 1 つも無い場合、`start()` は明確なエラーメッセージで失敗する(GUI 既定状態では必ず存在する)。
  - タイマーを保持している view が閉じられるとタイマーは死ぬ。v1 ではこれを検出しない。サーバー側 ping の失敗として顕在化し、ユーザーへ「マクロを再実行してください」と案内する(§10)。v1.1 で複数 view への登録・再アームを検討。
  - **注**: `GetInteractor()` によるタイマー駆動は本設計の要。実装最初期に ParaView 6.1.1 実機でのスパイク検証を必須とする(§13 M0)。

- 1 tick の処理: `selectors.select(timeout=0)` → accept / 受信バッファへ read / 完全な行(リクエスト)があれば**逐次実行** → 応答を送信バッファへ → 書き込み可能なら flush。大きな応答(スクリーンショット)は部分書き込みを許容し、複数 tick にまたがって送信バッファを排出する(EWOULDBLOCK 対応必須)。
  - 受信・送信バッファは**コネクション単位**で保持し、切断(EOF / 送信エラー)時はそのコネクションのバッファごと破棄する。完全な 1 行が揃うまで JSON パースは行わないため、部分受信・部分送信がプロトコル破損につながることは構造的にない。
- **再入ガード**: 長い `exec` の最中、ParaView の進捗表示等が Qt のイベント処理(`processEvents` 相当)を回すと `TimerEvent` が再発火し得る。`_on_tick` は先頭で実行中フラグを検査し、再入時は即 return する(これがないと exec の入れ子実行が起こり得る)。再入の実際の発生有無は M0 で観測する。
- 実行中は GUI がブロックする(フリーズする)。これは手動で重いフィルタを適用した場合と同じ挙動であり、仕様とする。
- 再起動安全・冪等: `start()` は既存インスタンスがあれば先に閉じてから bind する(マクロの再実行 = ブリッジ再起動)。bind 失敗(ポート使用中)時は当該ポートへ `ping` を打ち、同版のブリッジが応答すれば「起動済み」として正常終了、応答が無ければ「他プロセスがポートを使用中。`PARAVIEW_MCP_PORT` を両側で変更せよ」とエラー表示する。
- 起動確認の可視化: `start()` は bind 成功時に listening メッセージを、**最初のタイマー tick で「bridge active」を**それぞれ print する。後者が出ればタイマー駆動が実際に機能している証拠になる(M0 の検証観点を毎回の起動確認に組み込む)。
- バインド先は **127.0.0.1 固定**。ポートは引数 > 環境変数 `PARAVIEW_MCP_PORT` > 既定 `9911`。

### 4.2 MCP サーバー `src/paraview_mcp/`

- 依存: `mcp>=1.10,<2`、`Pillow`(スクリーンショット再圧縮用)。**`paraview` を import しない**。Python 3.10+。
- トランスポート: stdio。**stdout には MCP フレーム以外を一切書かない**。ログは stderr(+ 任意でファイル)。
- ブリッジへの接続: 遅延接続・永続コネクション。切断時は次のツール呼び出しで自動再接続(リトライ 2 回、計 ~2 秒)。失敗時はツールエラーとして「ParaView 側でブリッジを起動してください(Macros → paraview_mcp_bridge)」を返す。**サーバー自体は決して落とさない。**
- 直列化: ブリッジ呼び出し全体を `asyncio.Lock` で包み、同時に 1 リクエストのみ送る(MCP クライアントが並列ツールコールを出しても安全)。
- タイムアウト: ツール既定 120 秒(`execute_python` は引数で上書き可)。タイムアウトした `id` は放棄リストに載せ、遅れて届いた応答は黙って破棄する(§5.4)。
- 設定(環境変数): `PARAVIEW_MCP_HOST`(既定 127.0.0.1)、`PARAVIEW_MCP_PORT`(既定 9911)、`PARAVIEW_MCP_TOKEN`(任意)、`PARAVIEW_MCP_LOG`(ログファイルパス、任意)。

## 5. ワイヤプロトコル(サーバー ⇔ ブリッジ)

### 5.1 フレーミング

- **NDJSON**: 1 行 = 1 メッセージ(UTF-8、`json.dumps` は改行なし)。コード内の改行は JSON エスケープされるため衝突しない。
- 1 行の上限 64 MiB(超過時は `protocol_error` を返して接続を閉じる)。
- リクエストとレスポンスは `id` で対応付ける。ブリッジは受信順に処理する(パイプライン化可能だが v1 サーバーは同時 1 件しか送らない)。

### 5.2 リクエスト

```json
{"v": 1, "id": "req-42", "op": "exec", "token": "…任意…",
 "code": "Sphere(Radius=2)\nShow()\nGetActiveSource().GetDataInformation().GetNumberOfPoints()",
 "render": true}
```

| op | フィールド | 意味 |
|---|---|---|
| `ping` | — | 生存確認。ブリッジ/ParaView のバージョン、セッション種別を返す |
| `exec` | `code`(必須), `render`(既定 true) | コードを実行。`render=true` なら実行後に `Render()` を試みる |
| `reset` | `namespace`(既定 true), `pipeline`(既定 false) | 実行名前空間の初期化 / パイプライン全削除 |

- `token`: ブリッジ側に `PARAVIEW_MCP_TOKEN` が設定されている場合のみ検査。不一致は `auth_error`。
- 未知の `op` / `v` 不一致は `protocol_error`。

### 5.3 レスポンス

```json
{"v": 1, "id": "req-42", "status": "ok",
 "value": "482",
 "value_is_json": true,
 "stdout": "",
 "vtk_messages": "",
 "state": {"sources": [{"name": "Sphere1", "type": "Sphere", "visible": true, "active": true}],
            "view": {"type": "RenderView", "size": [1084, 802]},
            "time": null},
 "duration_ms": 18}
```

エラー時:

```json
{"v": 1, "id": "req-43", "status": "error",
 "error": {"kind": "exec_error", "type": "RuntimeError", "message": "…",
            "traceback": "Traceback (most recent call last): … (末尾 40 行に切詰め)"},
 "stdout": "", "vtk_messages": "…", "state": {…}, "duration_ms": 5}
```

- `error.kind` の分類: `exec_error`(ユーザーコード例外)/ `auth_error` / `protocol_error` / `internal_error`(ブリッジ自体の欠陥)/ `policy_violation`(**予約**。将来の静的検証フック(§14)がサーバー側で生成し、findings(規則名・理由・該当箇所)を同梱する。v1 では発生しない)。
- `state` は成功・失敗を問わず毎回付与する(§6.5)。`state` の生成自体が失敗した場合は `"state": null` とし、応答は返す。

### 5.4 タイムアウトと遅延応答

- タイムアウトはサーバー側の責務。ブリッジは実行を中断できない(GUI メインスレッドで実行中のため)。
- サーバーはタイムアウト時に当該 `id` を放棄済みとして記録し、LLM へは「実行は継続中の可能性がある。`get_state` で確認せよ」というエラーを返す。後から届いた同 `id` の応答は破棄する。
- サーバーは同時に 1 件しか送らない(§4.2 の直列化)ため、待機中の `id` は常に高々 1 つ。破棄規則は「**現在待機中の `id` 以外の応答行はすべて捨てる**」の 1 規則で足り、放棄 `id` の集合管理は不要。タイムアウト直後に送った次のリクエストは、ブリッジが前の実行を終えるまで処理されない(その待ち時間も含めてタイムアウトを見積もる)。
- ブリッジ実行中に接続が切れた場合、ブリッジは実行完了後に書き込み失敗を検知して当該コネクションを破棄する(実行結果は失われる。パイプライン状態は `get_state` で再取得可能)。

## 6. 実行セマンティクス(`exec`)

### 6.1 名前空間

- ブリッジはプロセス内に **単一の永続名前空間**(dict)を保持する(Jupyter のカーネル相当)。初回 `exec` 時に遅延初期化:

```python
ns = {"__name__": "__paraview_mcp__"}
exec("from paraview.simple import *", ns)
exec("from paraview import simple, servermanager", ns)
```

- 以後の `exec` は同一 `ns` で実行され、変数・関数定義が呼び出しをまたいで持続する。`reset(namespace=true)` で初期状態に戻す。

### 6.2 返り値の規約(最終式評価)

- コードを `ast.parse` し、**末尾の文が式(`ast.Expr`)ならその評価値を `value` として返す**(IPython/Jupyter と同じ規約。LLM が既知)。それ以外は `value: null`。

```python
tree = ast.parse(code)
last_expr = None
if tree.body and isinstance(tree.body[-1], ast.Expr):
    last_expr = ast.Expression(tree.body.pop().value)
exec(compile(tree, "<paraview-mcp>", "exec"), ns)
value = eval(compile(last_expr, "<paraview-mcp>", "eval"), ns) if last_expr else None
```

- シリアライズ: まず `json.dumps(value)` を試み、成功なら `value_is_json: true`。失敗(プロキシ等)なら `repr(value)` を送り `value_is_json: false`。`value` 文字列は 256 KiB で切詰め(`"…(truncated)"` を付記)。

### 6.3 出力の捕捉

- `stdout` / `stderr`: 実行中のみ `contextlib.redirect_stdout/stderr` で捕捉し、応答に載せる(各 64 KiB 切詰め)。
- **VTK メッセージ**: 実行中のみ `vtkStringOutputWindow` を `vtkOutputWindow.SetInstance()` で差し替え、`vtk_messages` として返す。実行後は必ず元のインスタンスへ復元する(GUI のメッセージ表示を壊さないこと)。VTK のエラーは Python 例外にならないことが多く、これが LLM のデバッグ能力を大きく左右する。
  - 捕捉範囲は vtkOutputWindow を通るもの(`vtkErrorMacro` / `vtkWarningMacro` 系)に限る。**vtkLogger 直行のログや、C++ が stdout/stderr へ直接書く出力は捕捉できない**(プロセスのコンソールに残る。仕様上の限界として README に記載)。
  - 差し替え中は GUI の Output Messages パネルへの表示が一時的にこちらへ逸れる(復元後は元に戻る)。pvserver 接続時に**サーバー側プロセス発のエラーがクライアントの vtkOutputWindow に乗るか**は M0 で確認する。
- 例外の捕捉は `BaseException` 単位で行い、`SystemExit` / `KeyboardInterrupt` も `exec_error` に変換する(`exit()` で GUI を殺さない)。ただし `os._exit()` やネイティブコードのクラッシュ(VTK の不正使用による segfault 等)はプロセスごと落ちるため防げない(§8 の前提どおり。サーバー側では切断として §10 の回復フローに乗る)。

### 6.4 レンダリング

- `render: true`(既定)のとき、実行成功後に `simple.Render()` を try/except 付きで呼ぶ。プロパティ変更のみのコードでも GUI の見た目が確実に追従するようにするため。

### 6.5 状態要約(`state`)

毎応答に付与する軽量サマリ。定義:

```json
{"sources": [{"name": str, "type": str(proxy XML 名), "visible": bool, "active": bool}, … 最大 50 件],
 "view":    {"type": str, "size": [int, int]} | null,
 "time":    {"value": float, "range": [float, float], "n_steps": int} | null }
```

- `GetSources()` / `GetActiveSource()` / `GetActiveView()` / `GetAnimationScene()` から構築。各値の取得は個別に try/except し、失敗した項目は null にする。`state` 生成の失敗が応答自体を失敗させてはならない(§5.3 の `"state": null`)。
- `visible` の判定に `GetRepresentation()` / `GetDisplayProperties()` を**使わない**(未存在の representation を作ってしまう副作用がある)。アクティブ view の `Representations` を列挙して `rep.Input` と突き合わせ、representation が無いソースは `visible: false` とする。
- コスト規律: summary ではサーバー往復を伴う呼び出し(`GetDataInformation` 等)を行わない(それらは `get_state(detail="arrays"/"full")` のみ)。50 件超過時は先頭 50 件+`"truncated": true`。目安として生成 20ms 以内(50 ソース時の実測を M0 で行う)。
- 目的: LLM がパイプラインの現在地を毎ターン確認でき、`get_state` を追加で呼ぶ頻度を減らす。

## 7. MCP ツール定義(サーバー側)

ツールは 5 個。**すべて内部的には `exec`(定型スニペット含む)へ還元される。**

### 7.1 `execute_python`

```
execute_python(code: str, timeout_s: int = 120, render: bool = True) -> structured
```

- 説明文(LLM 向け)に明記する内容: 名前空間は持続する / `paraview.simple` は import 済み / 末尾の式が返り値になる / 実行中 GUI はブロックする / `input()`・ダイアログ・`exit()` 禁止 / client-server 接続時のファイルパスはサーバー側で解決される。
- 返却(structured content): `{ok, value, stdout, vtk_messages, error, state, duration_ms}`。`traceback` はエラー時のみ。

### 7.2 `get_screenshot`

```
get_screenshot(max_width: int = 1280, quality: int = 80) -> Image + text
```

- 定型スニペット: ブリッジ側で `SaveScreenshot()` を一時ファイルへ実行 → bytes を読み base64 で `value` として返却 → 一時ファイル削除。
- サーバー側: base64 をデコードし、Pillow で `max_width` に縮小・JPEG(quality)再圧縮して MCP `Image` として返す。テキスト部に view 種別・元サイズ・バイト数を添える。
- 対象 view: アクティブ view が RenderView でなければ RenderView を探して撮る(スニペット内で解決)。

### 7.3 `get_state`

```
get_state(detail: Literal["summary", "arrays", "full"] = "summary") -> structured
```

- `summary`: §6.5 と同一。`arrays`: 各ソースの point/cell 配列名・成分数・レンジを追加。`full`: 各ソースの bounds・セル数・代表プロパティを追加。
- 定型スニペットで実装(ブリッジは関知しない)。

### 7.4 `reset_session`

```
reset_session(clear_pipeline: bool = True, clear_namespace: bool = True) -> text
```

- パイプライン全削除は「`GetSources()` を列挙し `Delete()`+Python 参照の `del`」の定型スニペットで行う。削除は依存の下流から先に行う(「他のソースから入力として参照されていないものを削除する」を空になるまで繰り返す実装)。
- `paraview.simple.ResetSession()` は **使わない**。これはセッション(サーバー接続)単位の再初期化であり、(a) pvserver 接続中に呼ぶと GUI が張っている接続を失い builtin へ戻る動きになり得る、(b) Qt 層(pqActiveObjects 等)が保持する接続・アクティブオブジェクトと Python 発の再初期化が同期される保証がない。「パイプラインの掃除」という目的に対して破壊半径が大きすぎる。

### 7.5 `bridge_status`

```
bridge_status() -> structured
```

- `ping` の結果(ブリッジ版数、ParaView バージョン、Python バージョン、セッション種別 builtin/client-server、接続先)+ サーバー側設定(host/port)を返す。トラブルシュートの一次窓口。

### 7.6 サーバー instructions(FastMCP `instructions`)

- 内容(要旨): 小さなステップで実行し、見た目を変えたら `get_screenshot` で確認せよ / 状態が不明なら `get_state` / プロキシ削除は `Delete(obj); del obj` / 大データの重い操作は時間がかかり GUI が固まるのは正常 — **長時間処理の前にはユーザーへ「ParaView が応答なしになるが正常」と一言告げ、`timeout_s` を明示的に引き上げよ** / 処理はなるべく段階に分割せよ / エラー時は `vtk_messages` も読め / `value` が切詰められたら、必要な数値だけを式で返すようコードを絞れ。誤字のない簡潔な英語で書き、旧実装の `default_prompt` は破棄する。
- `list_commands` 相当のツールは作らない(MCP のツール一覧で足りる)。

## 8. セキュリティ

- 本システムは**設計上、任意コード実行サービス**である。以下を前提・対策とする:
  - バインドは 127.0.0.1 のみ。リモート公開はサポートしない。
  - `PARAVIEW_MCP_TOKEN` を両側に設定した場合、全リクエストで照合(localhost 上の他プロセスからの接続対策)。README で設定を推奨。トークンは**静的な共有シークレット**であり、ローテーション・有効期限は設けない(変更 = 両側の環境変数を変えて再起動)。単一ユーザーマシンでは OS のユーザー境界が本来の防壁で、トークンは共有マシン向けの追加防御という位置付け。トークン値はログに書かない。認証失敗は stderr に記録する。
  - コード検証・サンドボックスは行わない(§2 非スコープ)。「MCP クライアント側の承認 UI が最後の門」であることを README に明記。
- 将来の静的検証は、サーバー側の送信前フック 1 点に集約する(§14)。

## 9. 配置・起動

### 9.1 リポジトリ構成

```
paraview_mcp/
  pyproject.toml                 # サーバーのみパッケージ化(setup.py は削除)
  src/paraview_mcp/
    __init__.py                  # paraview を import しない
    server.py                    # FastMCP アプリ・ツール定義・main()
    bridge_client.py             # 接続・フレーミング・リトライ・タイムアウト・遅延応答破棄
    snippets.py                  # get_screenshot / get_state / reset 用の定型コード(文字列定数)
  bridge/
    paraview_mcp_bridge.py       # 単一ファイル(embedded / standalone 両モード)
  tests/
    unit/                        # 偽ブリッジに対するプロトコル・クライアントのテスト
    integration/                 # pvpython + standalone ブリッジに対する E2E
  docs/DESIGN.md                 # 本書
```

- console script: `paraview-mcp = paraview_mcp.server:main`
- 旧 `src/paraview_manager.py` / `src/paraview_mcp_server.py` / eval 一式は v1 完成時に削除(必要な資産は snippets へ移植)。

### 9.2 起動手順(ユーザー視点)

1. ParaView GUI を起動(pvserver を使う場合は先に GUI から接続しておく。ブリッジはどちらでも同じ)。
2. `bridge/paraview_mcp_bridge.py` を **Macros → Import new macro** で登録し、マクロボタンをクリック(= `start()`)。Python Shell 実行でも可。成功時は Shell に `paraview-mcp bridge listening on 127.0.0.1:9911` を表示。
3. MCP クライアント設定(例: Claude Desktop):

```json
"mcpServers": {
  "paraview": {
    "command": "/path/to/venv/bin/paraview-mcp",
    "env": {"PARAVIEW_MCP_PORT": "9911"}
  }
}
```

- 自動起動(GUI 起動時にブリッジも起動)は v1 では提供しない。`paraview --script=` 等の起動オプションの互換性を M3 で調査の上、対応可否を決める。

### 9.3 WSL2 / Windows 混在構成

| 構成 | 可否 | 備考 |
|---|---|---|
| すべて WSL 内(Claude Code CLI + ParaView WSLg) | ○ | そのまま動く |
| すべて Windows | ○ | そのまま動く |
| Claude Desktop(Windows)+ ParaView(WSL) | ○ | **推奨: MCP サーバー自体を WSL 内に置き、Claude Desktop から `wsl.exe` 経由で起動する**(下記設定例)。TCP が WSL 内で完結し、WSL の localhost 転送機構に依存しない |
| ParaView(Windows)+ MCP サーバー(WSL) | △ | WSL → Windows 方向の localhost は既定(NAT モード)では届かない。WSL の mirrored ネットワーキング設定か、ホスト IP 指定+トークン設定が必要。非推奨 |

`wsl.exe` 経由の Claude Desktop 設定例(stdio は `wsl.exe` を素通しする):

```json
"mcpServers": {
  "paraview": {
    "command": "wsl.exe",
    "args": ["-d", "Ubuntu", "--", "/home/user/.venv/bin/paraview-mcp"]
  }
}
```

- MCP サーバーを Windows 側に置く場合(非推奨)は Windows → WSL の localhost 転送(NAT モードの `localhostForwarding`、既定で有効)に依存する。VPN やスリープ復帰後に転送が壊れる既知事例があるため、トラブル時はまず `wsl.exe` 方式へ切り替える。
- ファイルシステムが分かれる構成では、**データファイルのパスは ParaView 側のパスで指示する**(instructions に明記。スクリーンショットは base64 転送なので共有不要)。

### 9.4 ヘッドレス構成(GUI なし)

- `pvpython bridge/paraview_mcp_bridge.py --standalone` で GUI なし運用が可能(オフスクリーンレンダリング)。「AI が操作し人間はスクリーンショットで見る」最小構成、および CI がこのモードを使う。

## 10. エラー処理・回復(サーバー側の応答方針)

| 状況 | LLM へ返す内容 |
|---|---|
| ブリッジ未起動 / 接続不可 | 「ParaView でマクロ paraview_mcp_bridge を実行して起動せよ」+ 接続先 host:port。リトライ後も失敗ならこのメッセージ |
| 実行タイムアウト | 「処理は継続中の可能性がある。しばらく待って get_state / get_screenshot で確認せよ。GUI がフリーズ中なら処理中。次のリクエストは前の実行が終わるまで待たされる」 |
| TCP 接続は成立するが ping 無応答 | タイマー停止(ブリッジを載せた view が閉じられた等)の可能性が高い。「ParaView でマクロを再実行せよ」を案内 |
| exec_error | type / message / traceback 末尾 / vtk_messages をそのまま提示(LLM に自己修正させる) |
| 実行中の切断(応答喪失) | 「結果は失われたが実行は完了している可能性がある。get_state で確認せよ」 |
| auth_error / protocol_error / バージョン不一致 | 設定確認手順(トークン、ブリッジとサーバーの版数)を提示 |

## 11. テスト戦略 / CI

1. **unit(常時実行)**: `bridge_client` を偽ブリッジ(純 Python の NDJSON エコーサーバー)に対して検証 — フレーミング、部分書き込み、タイムアウト、遅延応答破棄、再接続。ブリッジ自体の exec セマンティクス(最終式評価・出力捕捉・切詰め)は paraview 部分をスタブ化してテスト。
2. **integration(paraview 必要)**: conda-forge の paraview を導入し、`pvpython --force-offscreen-rendering` + standalone ブリッジに対して E2E(Sphere 生成 → state 検証 → screenshot が有効な JPEG であること)。GitHub Actions では専用ジョブ(初期は allow-failure)。**担保範囲はプロトコル・exec セマンティクス・オフスクリーン描画の成立まで**。GUI 固有の経路(タイマー駆動、Qt との相互作用、実 GPU レンダリング)と公式バイナリ/conda 版の差異は integration では担保しない(手動スモークの担当)。画像は「有効な JPEG・期待サイズ」の検証に留め、ピクセル比較はしない。
3. **手動スモーク(リリース前)**: `docs/SMOKE.md` のチェックリスト(番号付き手順と期待結果: マクロ起動 → bridge_status → 生成・可視化 → screenshot → pvserver 接続版 → タイムアウト挙動 → reset → view を閉じた際の診断)に従い、ParaView 6.1.1 GUI(builtin / pvserver 接続の両方)で実施し、実施日と ParaView 版数を記録する。`tests/smoke/run_smoke.py`(実 GUI に一連のツール呼び出しを流し PASS/FAIL を印字するスクリプト)で操作部分を半自動化し、人間は画面の目視のみを担当する。SMOKE.md とスクリプトは M2 の成果物。

## 12. 既知の制約(仕様として許容)

- 実行中は GUI がフリーズする(メインスレッド実行の帰結。手動操作と同等)。フリーズは**リクエスト実行中のみ**で、アイドル時のタイマー(50ms、`timeout=0` の select)の負荷は体感不能なレベル。README に「実行中に GUI を操作しない / OS の『応答なし』表示で強制終了しない」を記載する。
- 実行中コードのキャンセル不可。
- タイマーを保持する RenderView が閉じられるとブリッジが停止し、マクロ再実行が必要。タイマーが死ぬと自己修復のためのコード実行機会そのものが失われるため、**ブリッジ側での完全な自動復旧は原理的に不可能**。サーバー側は「接続は成立するが ping 無応答」パターンとして検出し再実行を案内する(§10)。緩和策(複数 view への多重登録)は v1.1(§14)。
- 同時接続クライアントは実質 1(複数接続は受けるが実行は直列)。
- Python 参照が残る限り `Delete()` してもプロキシが解放されない ParaView 仕様は、instructions の注意書きで対処。

## 13. マイルストーン

- **M0: スパイク(最初に行う)** — ParaView 6.1.1 実機で「`GetInteractor().CreateRepeatingTimer` + オブザーバがマクロ起動のコールバックを GUI メインスレッドで駆動できること」「そのコールバック内で `paraview.simple` の生成系・`SaveScreenshot` が安定動作すること」を確認する。**ここが崩れた場合のみ設計を再検討**(代替: standalone モード + trame 案 B へ軸足移動)。追加確認項目: (a) 長い exec 中に `TimerEvent` が再入するか(§4.1 の再入ガードの実証)、(b) pvserver 接続時にサーバー側発の VTK エラーが `vtk_messages` に乗るか(§6.3)、(c) ソース 50 件時の state 要約の生成時間(§6.5)。
- **M1: MVP** — ブリッジ(embedded/standalone)+ サーバー(execute_python / get_screenshot / bridge_status)。手動マクロ起動。ユニットテスト。
- **M2: 堅牢化** — get_state / reset_session、タイムアウト・遅延応答・再接続の完全実装、VTK メッセージ捕捉、integration CI、README(セキュリティ注意・WSL 手順含む)。
- **M3: UX** — instructions チューニング(promptfoo の既存 eval 資産を再利用して回帰評価)、自動起動の調査、上流(LLNL)への還元判断。

## 14. 将来拡張(v1 ではフックのみ)

- **静的検証(別プロジェクト)**: サーバーの送信直前に `inspect(code: str) -> list[Finding]` 型のフック 1 点を設ける(entry point または設定でプラガブルに)。Finding が block 判定ならツールエラーとして返す。v1 は no-op。
- レシピ集(よくある可視化手順)の MCP リソース/プロンプト化。
- trame ビューア同居モード(案 B)を standalone ブリッジの発展形として提供。
- タイマーの多重化(v1.1): `start()` で存在する全 RenderView に登録し、さらに各 `exec` 時に未登録の view へ追加登録する。全登録 view が同時に閉じられない限りブリッジが生存する。GUI 起動時の自動ブリッジ起動(`paraview --script=` の互換性確認を含む)もここで扱う。
