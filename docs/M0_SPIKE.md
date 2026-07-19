# M0 スパイク手順書

DESIGN.md §13(M0)で定義した4つの未確認事項を、ParaView 6.1.1 実機で確認する。
本書はその具体的な実行手順。対象は以下の2シナリオのみ(Windows側ParaView GUI →
WSL側pvserverの検証は後回し、§9.3 の非推奨経路のため優先度が低い)。

- **シナリオ①**: WSL側で ParaView GUI を builtin(pvserver 無し)で起動
- **シナリオ②**: WSL側で pvserver を起動し、WSL側の ParaView GUI から接続

## 確認する4項目(DESIGN.md §13 と対応)

| # | 項目 | 何が崩れると困るか |
|---|---|---|
| (a) | `CreateRepeatingTimer` + `TimerEvent` オブザーバが GUI メインスレッドでコールバックを駆動し、その中で `paraview.simple` が使える | 崩れれば設計の前提が成立しない。standalone + trame(案B)へ再検討 |
| (b) | 長い `exec` の実行中に `TimerEvent` が再入するか(§4.1 の再入ガードが実際に必要か) | 再入する場合、ガード無しでは exec の入れ子実行が起き得る |
| (c) | pvserver 側で発生した VTK エラーが GUI プロセスの `vtkOutputWindow` に届くか(§6.3) | 届かない場合、client-server 構成での `vtk_messages` は無力という既知の限界が確定する |
| (d) | ソース50件時の state 要約生成コスト(§6.5) | 想定(目安20ms)を大きく超えるなら予算・実装方針の見直しが要る |

## 前提条件

- [ ] ParaView 6.1.1 が WSL 内にインストール済み(PATH を直接通す方式で問題ない。venv/conda 不要 — 理由は別途回答済み)
      - 確認: `pvpython --version` / `paraview --version` → `6.1.1` と出ること
- [ ] WSLg でGUIアプリが表示できる(このリポジトリの WSL では `DISPLAY=:0`, `WAYLAND_DISPLAY=wayland-0` が設定済みであることを確認済み)
- [ ] クライアント側の端末で `python3` が使える(確認済み: 3.12.3。標準ライブラリのみ使用するため ParaView 環境と無関係)

## 使うファイル

- [bridge/spike/m0_bridge_spike.py](../bridge/spike/m0_bridge_spike.py) — ParaView の Python Shell に貼り付けて実行する使い捨てブリッジ。**本番のブリッジ(§4.1, `bridge/paraview_mcp_bridge.py`)ではない。** M0 の4項目を検証するための最小実装で、認証・再接続・ポート競合処理などは省いている。
- [bridge/spike/m0_client.py](../bridge/spike/m0_client.py) — 別ターミナルから `python3` で実行するテストクライアント。ParaView 非依存。

両ファイルとも、①②のどちらのシナリオでも**まったく同じものを使う**。これ自体が「pvserver の有無に関わらずブリッジ側は同一コードで動く」という設計の主張の検証になる。

## シナリオ①: WSL builtin(pvserver 無し)

1. ターミナルで `paraview` を起動(WSLg 経由でウィンドウが開く)。
2. `View → Python Shell` を開く。
3. `m0_bridge_spike.py` の中身を Python Shell に貼り付けて実行する(あるいは `exec(open('/path/to/bridge/spike/m0_bridge_spike.py').read())`)。
   - 期待する出力:
     ```
     [M0 hh:mm:ss] listening on 127.0.0.1:9911, timer_id=1
     [M0 hh:mm:ss] waiting for first tick... (run m0_client.py from another terminal)
     [M0 hh:mm:ss] bridge active (first tick fired) -- checkpoint (a) confirmed
     ```
   - 3行目が**数百ms以内**に出ない、または全く出ない場合 → **(a) 不合格**。この時点で M0 の中止基準に該当するので、以降のシナリオは実施せず DESIGN.md M0 の代替案検討に進む。
4. 別ターミナルで疎通確認:
   ```bash
   python3 bridge/spike/m0_client.py ping
   ```
   `"status": "ok"` が返れば TCP 疎通と実行の往復が成立(a の追加確証)。
5. 再入テスト:
   ```bash
   python3 bridge/spike/m0_client.py reentry
   ```
   実行中、ParaView の Python Shell 側に `*** REENTRY DETECTED ***` が出るかを観察する。出ても出なくても結果として記録する(§4.1 のガードは設計に既に入っているので、ここは「ガードが実際に発火する状況が存在するか」を確認する位置づけ)。
6. state 生成コストテスト:
   ```bash
   python3 bridge/spike/m0_client.py state50
   ```
   出力される `elapsed_ms` を記録シートに書く。

## シナリオ②: WSL pvserver + WSL ParaView 接続

1. 別ターミナルで pvserver を起動する(既定ポート 11111):
   ```bash
   pvserver
   ```
2. `paraview` を起動 → `File → Connect` → `Add Server` で Host=`localhost`, Port=`11111` を設定して接続する。パイプラインブラウザやタイトルバーに接続状態(remote server)が表示されることを確認する。
3. Python Shell に `m0_bridge_spike.py` を貼り付けて実行する。**シナリオ①と全く同じ手順・同じファイル。**
   - 起動ログの出方に①との違いが無いかも記録する(違いがあれば「pvserver 接続はブリッジの起動に影響しない」という前提が崩れている可能性がある)。
4. `ping` → `reentry` → `state50` を再実行し、①の結果と比較する。差異があれば記録する(無いことが期待値)。
5. VTK エラー伝搬テスト((c) 専用、①では実施不可):
   ```bash
   python3 bridge/spike/m0_client.py servererror
   ```
   このテストは一時的な不正な `.vtk` ファイルを作らせて `OpenDataFile` させる。client-server 接続下ではファイル読み込み・パースは **pvserver プロセス側**で起きるため、ここで出るエラーは「サーバー側発」のはずである。
   - `vtk_messages` にエラー文言が含まれる → **(c) 合格**。
   - `vtk_messages` は空だが、ParaView の `View → Output Messages` パネルには同じエラーが表示されている → **(c) 不合格**。client-server の RMI 中継と `vtkOutputWindow` 差し替えの間に構造的な壁があるという重要な否定的知見。DESIGN.md §6.3 に確定事項として反映する。
   - どちらにも何も出ない → トリガーが弱すぎる可能性がある。手動で別の不正入力(存在しない配列を `ColorBy` に指定して `Render()` する、壊れた別形式のファイルを開く等)を試してから判定する。

## 記録シート

| チェックポイント | シナリオ① | シナリオ② | 判定 |
|---|---|---|---|
| (a) タイマー駆動(初回tickまでの時間) | 数百ms以内、`bridge active`確認済み。`tick_count`も継続増加 | pvserver接続下でも同様に動作 | **PASS** |
| (b) 再入(観測されたか、最大深さ) | `sleep`+`Render()`を2秒間ループさせても`REENTRY DETECTED`は0件 | 同様に0件 | 観測**無し**(ガードは保険として温存) |
| (c) VTKエラー伝搬(server→client) | 対象外 | サーバー側`vtkPolyDataReader`のパースエラーが`vtk_messages`に到達。ParaView Shellログ(`vtkPolyDataReader.cxx:140 ERR\| ... Cannot read number of points!`)と完全一致(同一オブジェクトポインタ`0x1f6e4d60`) | **PASS** |
| (d) state生成コスト(50件、ms) | 13.91ms | 14.952ms(球体50個をパイプラインブラウザで目視確認済み) | **PASS**(目安20ms以内) |

- ParaView バージョン: 6.1.1 (MPI-Linux-Python3.12-x86_64)
- 実施日: 2026-07-19
- 特記事項:
  - `m0_bridge_spike.py`の初期実装にバグがあり(`_on_writable`のEVENT_WRITE解除タイミング)、クライアントが応答受信直後にソケットを閉じるとサーバー側で`ValueError: Invalid file descriptor: -1`が発生していた。修正済み(即時ダウングレード+`closed`フラグによる二重クローズ防止)。実害はなく(クライアントは正常に応答を受信できていた)、サーバー側のクリーンアップ処理のみのバグだった。
  - (c)の初回試行は誘発コードが弱く失敗した: 中身が完全なデタラメの`.vtk`ファイルを`OpenDataFile()`に渡すと、実際のVTK C++リーダーに到達する前に`paraview.simple`側のPythonレベルのファイル形式スニッフィング(拡張子・ヘッダ判定)が`RuntimeError("No reader found")`を送出して止まる。この経路は`vtkOutputWindow`を一切通らない。正しく検証するには、レガシーVTKのヘッダ(`# vtk DataFile Version 3.0`)だけ本物にしてスニッフィングを通過させ、ボディ側を壊して実パーサに到達させる必要があった。M1実装時、`OpenDataFile`が投げるPython例外と`vtk_messages`は別経路である点を踏まえておくこと。

## 結論

M0の4項目すべて確認完了(2026-07-19)。DESIGN.md の timer-driven アーキテクチャの前提はいずれも崩れなかった。M1(ブリッジ本実装 + MCPサーバー実装)に進んで良い。

## 判定基準・次アクション(DESIGN.md M0 を踏襲)

- **(a) が両シナリオで失敗** → 最優先の中止基準。DESIGN.md の M0 節に記載の代替(standalone モード + trame、案B)へ軸足移動を検討する。
- **(b) で再入が観測された** → §4.1 の再入ガードは必須と確定(すでに設計・スパイク実装済み)。M1 実装時にこのワークロードを回帰テストのケースとして残す。観測されなければガードは保険として残すのみで、設計変更は不要。
- **(c) が不合格(伝搬しない)** → §6.3 の限界として明記済みの内容が「確認された既知の限界」に格上げされる。README のセキュリティ/制約セクションに一文追加する。pvserver 構成での VTK エラーは「Output Messages パネルを見よ」という案内が必要になる。
- **(d) が20ms を大きく超過** → §6.5 の予算・実装(50件キャップ、`arrays`/`full` への分離)を見直す。

## 既知の問題: 2回目以降の ParaView 起動で Python Shell 起動時にフリーズする

**症状**: 1回目の起動は正常。Ctrl+C 等でプロセスを強制終了させた後、2回目以降の起動で
`View → Python Shell` を開くとフリーズし、キーボード割り込み以外に脱出手段が無い。
ParaView プロセスの完全終了や WSL 自体の再起動でも直らない(= プロセス内メモリの問題ではなく、
ディスクに永続化された状態が原因)。

**原因(推定)**: `~/.config/ParaView/ParaView.ini` の `[MainWindow] Layout=` に、
強制終了させた直前セッションのウィンドウ配置(`QMainWindow::saveState()` のバイナリblob)が
不完全な形で書き残され、次回起動時の `restoreState()` がこれを復元しようとして固まる。
`qt.qpa.plugin: Could not find the Qt platform plugin "wayland" in ""` という警告も併発するが、
これは WSLg が `DISPLAY` と `WAYLAND_DISPLAY` を両方公開しているために Qt が wayland を
試して失敗し xcb にフォールバックしているだけで、**フリーズの直接原因ではなかった**
(`QT_QPA_PLATFORM=xcb` を付けずに設定リセットのみで解消したため)。

**対処**:
```bash
mv ~/.config/ParaView ~/.config/ParaView.bak   # 削除ではなくリネームで退避
paraview
```
以後、ParaView をキーボード割り込みや `kill` で強制終了させた場合は、次回起動前に
同様の設定リセットが必要になる可能性がある。正常終了(ウィンドウの×やメニューの Exit)
であれば `Layout` は正しく保存されるため、通常はこの問題は起きないはずと考えられる
(未検証)。

**副作用**: 設定リセットにより `GeneralSettings.ShowWelcomeDialog` 等が既定値に戻る。
起動時に `xdg-open: no method available for opening 'file:///...'` という警告が
大量に出ることがあるが、これはブラウザ/ファイラー未インストールのミニマルな WSL 環境で
Welcome 画面か何かが `xdg-open` を叩いて失敗しているだけの無害なノイズ。

## 後回し

- Windows側 ParaView GUI → WSL側 pvserver 接続の検証(DESIGN.md §9.3 の非推奨経路)は、①②の結果が出てから着手する。
