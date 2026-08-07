# SeminarReportHelper

セミナー録画（.mp4）から、画像付きの日本語レポートを生成し Confluence に反映するツール。

- **文字起こしはローカル完結**（faster-whisper）。音声・動画を外部に送信しない
- **レポート駆動キャプチャ** — AI が本文中で「図で示すべき」と判断した箇所の映像だけを自動で切り出す
- **文字数・詳細度を選択可能**（簡潔 / 標準 / 詳細 ＋ 任意の目標文字数）
- **章立ては内容に応じて AI が設計**（固定テンプレートではない）
- Confluence へは **REST API で自動投稿**、または ZIP 出力して手動で貼り付け

---

## 仕組み

一般的な「シーン変化を検出してスライドを機械的に集める」方式ではなく、順序を逆にしている。

```
文字起こし（タイムスタンプ付き）
      ↓
AI が章立てを設計し、本文を執筆
      ↓  本文中に [[capture:01:23:45|キャプション]] を埋め込む
AI が重要と判断した箇所だけが画像の候補になる
      ↓
その時刻の周辺 5 枚から最良のフレームを選抜
      ↓
Markdown / Confluence storage format にレンダリング
```

これにより画像枚数が本文の重要度と一致し、不要なスライド画像が混ざらない。

LLM が実在しない時刻を返す可能性があるため、`report/markers.py` で
**文字起こしのセグメント境界への吸着**・**範囲外マーカーの破棄**・
**開始 +2 秒のオフセット**（切り替わり途中のフレームを避ける）を機械的に適用している。

---

## LLM プロバイダと費用

レポート生成には LLM を使う。プロバイダによって費用が異なるので、
API キーを取得する前に確認してほしい。

| プロバイダ | 費用 | 備考 |
|---|---|---|
| `claude` | 従量課金（要クレジットカード登録） | 品質重視。動画の長さ・詳細度によりコストは変動する |
| `openai` | 従量課金（要クレジットカード登録） | 同上 |
| `ollama` | **無料**（ローカル実行） | 別途 [Ollama](https://ollama.com) 本体とモデル（数GB）のダウンロードが必要。Vision（`--verify-captures`）は自動でスキップされる |

`claude` / `openai` は Claude.ai や ChatGPT の月額契約とは別会計の API 利用料。
最新の料金は各社の公式ページ（[Anthropic](https://www.anthropic.com/pricing) /
[OpenAI](https://openai.com/pricing)）を参照。

費用をかけずに試したい場合は `.env` の `SRH_LLM_PROVIDER=ollama` を選ぶ。

---

## セットアップ

```bash
uv venv
uv pip install -e ".[asr,llm,dev]"

cp .env.example .env   # API キー等を記入
```

`ffmpeg` はシステムに入っていればそれを使い、無ければ `imageio-ffmpeg` 同梱の
バイナリに自動でフォールバックするため、追加インストールは不要。

### 依存の内訳

| extras | 内容 |
|---|---|
| `asr` | faster-whisper（文字起こし）|
| `llm` | anthropic / openai SDK。Ollama のみ使う場合は不要 |
| `dev` | pytest |

### GPU を使う（強く推奨）

**処理時間の 8〜9 割は文字起こしが占める。** NVIDIA GPU があれば 10〜30 倍速くなるので、
搭載機では必ず有効にしてほしい。CUDA ライブラリを入れるだけでよい。

```bash
uv pip install nvidia-cublas-cu12 nvidia-cudnn-cu12
```

`pip install`（`uv` を付けない）だと `.venv` の外に入り、ここからは見えないので注意。

Windows では、pip で入れた DLL は `site-packages/nvidia/*/bin/` に置かれるだけで
自動では読み込まれない（Python 3.8 以降、拡張モジュールの依存 DLL は PATH から
探索されない）。本ツールは起動時に `os.add_dll_directory()` で登録するため
追加の設定は不要。

有効になっているかは診断コマンドで確認できる。

```bash
uv run seminar-report doctor
```

`デバイス : cuda / float16` と出ていれば GPU が使われている。
`cpu / int8` なら CPU 実行で、30 分の動画に 15〜40 分かかる。

CUDA ライブラリが揃っていない場合は自動的に CPU へ縮退するため、
GPU が無い環境でもそのまま動作する。

### 処理時間の目安（30 分の動画・標準）

| 環境 | 文字起こし | 全体 |
|---|---|---|
| GPU（CUDA 有効）| 1〜3 分 | **3〜6 分** |
| CPU のみ | 15〜35 分 | 20〜40 分 |

初回のみ Whisper モデル（medium で約 1.5GB）のダウンロードが入る。

---

## 使い方

### Web UI

```bash
uv run seminar-report serve
# → http://127.0.0.1:8000
```

mp4 をドロップ → 詳細度を選択 → 生成 → プレビュー画面で本文編集・画像の
差し替え/除外 → ZIP ダウンロードまたは Confluence 投稿。

処理には 1 時間の動画で 10〜30 分ほどかかるため、進捗は SSE でリアルタイムに表示される。

「完了時にレポートを新しいタブで開く」（既定でオン）にチェックが入っていると、
生成開始と同時に空タブが開き、完了した瞬間に `report.html` が読み込まれる。
ZIP をダウンロードして展開して開く手間が省ける。

「レポートへの要望」欄に自由記述で指示すると、章立て・本文の重み付け・
画像キャプチャの選定に反映される。例:

```
1. 特に価格の話を重視したレポートにして
2. 導入事例のみに対してレポートにして
3. 競合比較については触れない(省いた)レポートにして
```

### CLI

```bash
# 標準の詳細度で生成
uv run seminar-report run seminar.mp4

# 文字数を指定（--detail より優先。画像枚数も自動で連動する）
uv run seminar-report run seminar.mp4 --chars 4000

# 詳細版を作り、そのまま Confluence に投稿
uv run seminar-report run seminar.mp4 -d detailed --publish --space ENG

# 登壇者映像やロゴを除き、スライド部分だけを画像として切り出す
# left,top,right,bottom を画面全体に対する割合(0〜1)で指定する
uv run seminar-report run seminar.mp4 --crop 0.02,0.13,0.76,0.87

# 生成済みレポートを後から投稿
uv run seminar-report publish output/seminar --space ENG

# 文字起こしだけ（結果はキャッシュされる）
uv run seminar-report transcribe-only seminar.mp4

# 実行環境の診断（GPU が使えているかの確認）
uv run seminar-report doctor
```

主なオプション:

| オプション | 説明 |
|---|---|
| `-d, --detail` | `brief` / `standard` / `detailed` |
| `-c, --chars` | 目標文字数。指定すると `--detail` より優先される |
| `--max-captures` | 画像の最大枚数 |
| `-p, --provider` | `claude` / `openai` / `ollama` |
| `-m, --model` | 使用するモデル名（未指定なら `.env` の既定値）|
| `--whisper-model` | `tiny` 〜 `large-v3`（既定 `medium`）|
| `--verify-captures` | Vision で画像の有用性を検証する（品質↑・コスト↑）|
| `--crop` | キャプチャの切り出し矩形 `left,top,right,bottom`（0〜1の割合）。登壇者映像やロゴを除きたい場合に指定 |
| `--request` | レポート内容への要望（例: `'特に価格の話を重視して'` `'導入事例には触れない'`） |
| `--no-images` | テキストのみのレポート |
| `--no-cache` | 文字起こしキャッシュを使わない |

### 詳細度プリセット

| プリセット | 目標文字数 | セクション数 | 画像枚数 |
|---|---|---|---|
| `brief` 簡潔 | 800 | 3〜4 | 3 |
| `standard` 標準 | 2,500 | 5〜7 | 6 |
| `detailed` 詳細 | 6,000 | 8〜12 | 12 |

`--chars` を指定した場合は、その文字数からセクション数と画像枚数を自動算出する。

---

## 出力

```
output/<動画名>/
├── report.md                # Markdown（画像は images/ を参照）
├── report.confluence.xml    # Confluence storage format
├── report.html              # 単体で開ける HTML（画像を base64 で埋め込み済み）
├── report.json              # 再投稿・再編集用
├── transcript.json          # 文字起こし
├── images/                  # 採用されたキャプチャ
└── report_bundle.zip        # 上記一式
```

Confluence への貼り方は環境によって使える機能が異なるため、3通り用意している。

| 方法 | 手順 |
|---|---|
| 自動投稿（推奨） | `.env` に Confluence の接続情報を設定し `--publish` / Web UI の「投稿」ボタン |
| ソース編集（「マークアップ」）がある場合 | `report.confluence.xml` の中身をページの「マークアップ」に貼り、`images/` の画像をまとめて添付 |
| 上記が使えない場合 | `report.html` をブラウザで開き、全選択してコピーし、Confluence の編集画面にそのまま貼り付ける（表示された画像がそのまま添付・埋め込みされることが多い） |

Confluence の「マークダウン」マクロは `report.md` の画像パスをうまく自動リンクしないことがあるため、その場合は `report.html` を使う方法を試すこと。

---

## Confluence 連携

`.env` に以下を設定する（API トークンは
[Atlassian のアカウント設定](https://id.atlassian.com/manage-profile/security/api-tokens)から発行）。

```
CONFLUENCE_BASE_URL=https://your-org.atlassian.net
CONFLUENCE_EMAIL=you@example.com
CONFLUENCE_API_TOKEN=...
CONFLUENCE_SPACE_KEY=ENG
```

投稿は必ず 3 段階で行われる。Confluence は存在しない添付への参照を許さないため。

1. 画像参照を除いた本文でページを作成
2. 画像を添付
3. 画像参照を含む本文で更新

---

## キャッシュ

文字起こしはパイプライン中で最も重い工程なので、動画の SHA256 とモデル名を
キーに `.cache/` へ保存される。**同じ動画で詳細度だけ変えて作り直す場合は
文字起こしを丸ごとスキップする**ため数十秒で完了する。

---

## 困ったときは

まず `uv run seminar-report doctor` を実行する。多くはここで原因が分かる。

| 症状 | 原因と対処 |
|---|---|
| `ValidationError` が出て起動しない | 古い `.env` の空欄が原因（`SRH_WHISPER_BEAM_SIZE=` など）。該当行をコメントアウトするか削除する |
| ブラウザに見慣れない JSON が出る | ポート 8000 を別アプリ（Epic Games Launcher など）が使用中。`--port 8001` で起動し直す |
| アップロードが終わらない | 数百MB〜GB の動画は転送に数分かかる。進捗バーの数値が伸びていれば正常 |
| `cublas64_12.dll is not found` | CUDA ライブラリが未導入か、`pip install`（`uv` 無し）で別の Python に入っている。`uv pip install nvidia-cublas-cu12 nvidia-cudnn-cu12` を実行し、`doctor` の「CUDA ライブラリ」欄を確認する |
| `` `temperature` is deprecated `` | Claude Opus 4.7 以降と Sonnet 5 では廃止されたパラメータ。本ツールは送らないので、古いコードのままなら `git pull` する |
| 処理がとにかく遅い | GPU が使われていない。`doctor` で `デバイス : cpu` なら [GPU を使う](#gpu-を使う強く推奨)を参照 |
| 最初の数分、進捗が動かない | 初回の Whisper モデル DL（約 1.5GB）。「モデルを準備しています」と表示される |
| 「接続が切れました」と出た | ジョブはサーバー側で継続中。自動でポーリングに切り替わり結果まで進む。ブラウザを閉じても、開き直せば復帰する |
| ブラウザを閉じてしまった | 再度開けば直前のジョブに自動復帰する |

処理中のジョブはサーバープロセスが持っているため、**uvicorn を停止すると失われる**。
`--reload` を付けているとコード変更のたびに再起動がかかるので、実運用では外すこと。

---

## 開発

```bash
uv run pytest
```

結合テストは ffmpeg で 70 秒のテスト動画を合成し、Whisper と LLM だけを
モックしてパイプライン全体を通している（フレーム抽出・選抜・レンダリングは実物）。

### 構成

| パス | 役割 |
|---|---|
| `seminar_report/pipeline.py` | 全体のオーケストレーション。CLI / Web UI の唯一の入口 |
| `seminar_report/transcribe/` | faster-whisper とキャッシュ |
| `seminar_report/llm/` | プロバイダ抽象（Claude / OpenAI / Ollama）とプロンプト |
| `seminar_report/report/` | 詳細度、map-reduce 生成、マーカー検証 |
| `seminar_report/media/` | ffmpeg 制御、フレーム抽出と品質評価 |
| `seminar_report/render/` | Markdown / Confluence storage format |
| `seminar_report/publish/` | Confluence REST API |
| `seminar_report/web/` | FastAPI ＋ 素の HTML/JS（ビルド工程なし）|
