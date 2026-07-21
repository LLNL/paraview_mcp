# ParaView MCP

自然言語で ParaView を操作するための MCP(Model Context Protocol)サーバー。Claude Desktop / Claude Code などの MCP クライアントから、実行中の ParaView に Python コードを送り、可視化の作成・操作・スクリーンショットによる確認までを AI に任せられる。

本リポジトリは [LLNL/paraview_mcp](https://github.com/LLNL/paraview_mcp) のフォークで、接続方式を全面的に再設計している(仕様の正: [docs/DESIGN.md](docs/DESIGN.md))。上流実装が依存していた pvserver のコラボレーション同期機能(現行 ParaView では非推奨・不安定)を廃し、**ParaView 内で動く小さなブリッジへコード文字列を送って実行する**方式に置き換えた。

## 仕組み

```
Claude          ◄─ stdio (MCP) ─►  paraview-mcp サーバー  ◄─ TCP 127.0.0.1:9911 ─►  ParaView 内ブリッジ
(Desktop/Code)                     純 Python・paraview 非依存      (NDJSON)          GUI メインスレッドで exec
```

- **ブリッジ**([bridge/paraview_mcp_bridge.py](bridge/paraview_mcp_bridge.py)): 単一ファイル・標準ライブラリのみ。ParaView の組み込み Python 内で動き、localhost の TCP で受け取ったコードを GUI メインスレッドで実行する。マクロとして一度登録すれば以後はワンクリックで起動できる。
- **MCP サーバー**(`paraview-mcp`): 通常の Python 環境で動き、`paraview` パッケージに依存しない。ParaView とのバージョン整合問題が構造的に発生しない。
- GUI が pvserver に接続している場合は、GUI が張っている既存セッションをそのまま使う。pvserver 側の設定変更や `--multi-clients` は不要。

## 必要環境

- ParaView 6.1.1(実機検証済み。ブリッジは 5.11+ / Python 3.9+ で動くことを目標に実装)
- MCP サーバー側: Python 3.10+ と [uv](https://docs.astral.sh/uv/)(推奨)

## セットアップ

```shell
git clone https://github.com/tatebayashit/paraview_mcp.git
cd paraview_mcp
uv sync
```

## 使い方

### 1. ParaView 側: ブリッジを起動する

1. ParaView を起動する(pvserver を使う場合は先に File → Connect で接続しておく)。
2. ブリッジを起動する:
   - **通常(builtin)**: Macros → Import new macro… で `bridge/paraview_mcp_bridge.py` を登録し、マクロを実行する。
   - **pvserver 接続時**: マクロ登録は使わず、`bridge/paraview_mcp_bridge.py` の内容を View → Python Shell に貼り付けて実行する(後述の「既知の問題」参照)。
3. 次の 2 行が出れば成功(2 行目がタイマー駆動が機能している証拠):

```
[paraview-mcp HH:MM:SS] listening on 127.0.0.1:9911
[paraview-mcp HH:MM:SS] bridge active (first tick fired)
```

### 2. MCP クライアント側: サーバーを登録する

Claude Desktop(`claude_desktop_config.json`):

```json
"mcpServers": {
  "paraview": {
    "command": "uv",
    "args": ["run", "--directory", "/path/to/paraview_mcp", "paraview-mcp"]
  }
}
```

Claude Code:

```shell
claude mcp add paraview -- uv run --directory /path/to/paraview_mcp paraview-mcp
```

Windows の Claude Desktop から WSL 内のサーバーを使う場合は「WSL2 / Windows 混在構成」を参照。

### 提供ツール

| ツール | 内容 |
|---|---|
| `execute_python(code, timeout_s=120, render=True)` | ParaView 内で Python コードを実行する。`paraview.simple` は import 済み、名前空間は呼び出しをまたいで持続、末尾の式の値が返る(IPython 規約)。毎応答にパイプライン状態の要約(`state`)が付く |
| `get_screenshot(max_width=1280, quality=80)` | アクティブな RenderView を JPEG で取得する(base64 転送のためファイル共有は不要) |
| `bridge_status()` | ブリッジ疎通・ParaView バージョン・セッション種別(builtin / client-server)の確認。トラブル時の一次窓口 |

`get_state`(詳細な状態取得)と `reset_session`(パイプライン / 名前空間のリセット)は M2 で追加予定([docs/M2_PLAN.md](docs/M2_PLAN.md))。

### 環境変数

| 変数 | 既定 | 意味 |
|---|---|---|
| `PARAVIEW_MCP_PORT` | 9911 | ブリッジの待ち受け / サーバーの接続先ポート(両側で一致させる) |
| `PARAVIEW_MCP_HOST` | 127.0.0.1 | サーバー側の接続先(通常変更不要) |
| `PARAVIEW_MCP_TOKEN` | なし | 設定すると全リクエストで照合される共有シークレット(両側に同じ値を設定) |
| `PARAVIEW_MCP_LOG` | なし | サーバーのログをファイルにも書く場合のパス |

### WSL2 / Windows 混在構成

| 構成 | 可否 | 備考 |
|---|---|---|
| すべて WSL 内 / すべて Windows | ○ | そのまま動く |
| Claude Desktop(Windows)+ ParaView(WSL) | ○ | **推奨: サーバーも WSL 内に置き、`wsl.exe` 経由で起動する**(下記)。TCP が WSL 内で完結する |
| ParaView(Windows)+ サーバー(WSL) | △ | 既定の NAT モードでは WSL → Windows 方向の localhost が届かない。非推奨 |

```json
"mcpServers": {
  "paraview": {
    "command": "wsl.exe",
    "args": ["-d", "Ubuntu", "--", "/home/user/paraview_mcp/.venv/bin/paraview-mcp"]
  }
}
```

ファイルシステムが分かれる構成では、データファイルのパスは **ParaView が動いている側のパス**で指示する(スクリーンショットは base64 転送なので共有不要)。

### ヘッドレス運用(GUI なし)

```shell
pvpython --force-offscreen-rendering bridge/paraview_mcp_bridge.py --standalone [--port 9911]
```

GUI を使わず「AI が操作し、人間はスクリーンショットで確認する」最小構成。CI もこのモードを使う。

## セキュリティ上の注意

- 本システムは設計上、**任意コード実行サービス**である。MCP クライアント(LLM)が生成した任意の Python コードが ParaView プロセスの権限で実行される。コードの静的検証・サンドボックス化は行わない。**MCP クライアント側のツール実行承認 UI が最後の防壁**であることを理解した上で使うこと。
- ブリッジは 127.0.0.1 にのみバインドする。リモート公開はサポートしない。
- 共有マシンでは `PARAVIEW_MCP_TOKEN` の設定を推奨する(localhost 上の他プロセスからの接続対策)。

## 既知の制約

- コード実行中は ParaView GUI がフリーズする(メインスレッド実行のため。手動で重いフィルタを適用した場合と同じ挙動)。OS が「応答なし」と表示しても強制終了しないこと。
- 実行中コードのキャンセルはできない。
- ブリッジのタイマーを載せた RenderView を閉じるとブリッジが停止する。`bridge_status` が再実行を案内するので、ブリッジを起動し直す。
- vtkOutputWindow を通らない出力(vtkLogger 直行のログ、C++ からの stdout/stderr 直書き)は `vtk_messages` に捕捉されない(プロセスのコンソールに残る)。
- **既知の問題**: pvserver 接続中に**マクロ登録経由**でブリッジを起動すると ParaView がセグメンテーション違反で落ちる。未変更の検証用コードでも再現するため ParaView 側の問題と切り分け済み(詳細: [docs/M1_PLAN.md](docs/M1_PLAN.md) §5 #8)。**回避策: pvserver 接続時は Python Shell への貼り付けで起動する**(builtin ではマクロ登録で問題ない)。

## 開発

```shell
uv sync
uv run pytest tests/unit     # 76 件、ParaView 不要
uv run ruff check bridge/ src/ tests/
```

- unit CI: [.github/workflows/unit.yml](.github/workflows/unit.yml)(Python 3.10〜3.12)
- ロードマップ([docs/DESIGN.md](docs/DESIGN.md) §13): M0 スパイク **完了** → M1 MVP **完了**(2026-07-19)→ M2 堅牢化 **計画済み・実装未着手**([docs/M2_PLAN.md](docs/M2_PLAN.md))→ M3 UX

## 上流プロジェクト

本リポジトリは LLNL の ParaView-MCP のフォークであり、BSD-3-Clause ライセンスを維持している([LICENSE](LICENSE) / [NOTICE](NOTICE))。上流のオリジナル実装のデモと論文:

[![Video Title](https://img.youtube.com/vi/GvcBnAcIXp4/maxresdefault.jpg)](https://youtu.be/GvcBnAcIXp4)

S. Liu, H. Miao, and P.-T. Bremer, “Paraview-MCP: Autonomous Visualization Agents with Direct Tool Use,” in Proc. IEEE VIS 2025 Short Papers, 2025.

```bibtex
@inproceedings{liu2025paraview,
  title={Paraview-MCP: Autonomous Visualization Agents with Direct Tool Use},
  author={Liu, S. and Miao, H. and Bremer, P.-T.},
  booktitle={Proc. IEEE VIS 2025 Short Papers},
  pages={00},
  year={2025},
  organization={IEEE}
}
```

Paraview_MCP was originally created by Shusen Liu (liu42@llnl.gov) and Haichao Miao (miao1@llnl.gov).

LLNL-CODE-2007260
