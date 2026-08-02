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

### CLI

```bash
# 標準の詳細度で生成
uv run seminar-report run seminar.mp4

# 文字数を指定（--detail より優先。画像枚数も自動で連動する）
uv run seminar-report run seminar.mp4 --chars 4000

# 詳細版を作り、そのまま Confluence に投稿
uv run seminar-report run seminar.mp4 -d detailed --publish --space ENG

# 生成済みレポートを後から投稿
uv run seminar-report publish output/seminar --space ENG

# 文字起こしだけ（結果はキャッシュされる）
uv run seminar-report transcribe-only seminar.mp4
```

主なオプション:

| オプション | 説明 |
|---|---|
| `-d, --detail` | `brief` / `standard` / `detailed` |
| `-c, --chars` | 目標文字数。指定すると `--detail` より優先される |
| `--max-captures` | 画像の最大枚数 |
| `-p, --provider` | `claude` / `openai` / `ollama` |
| `--whisper-model` | `tiny` 〜 `large-v3`（既定 `medium`）|
| `--verify-captures` | Vision で画像の有用性を検証する（品質↑・コスト↑）|
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
├── report.json              # 再投稿・再編集用
├── transcript.json          # 文字起こし
├── images/                  # 採用されたキャプチャ
└── report_bundle.zip        # 上記一式
```

Confluence に手で貼る場合は、`report.confluence.xml` の中身をページの
「ソース編集」に貼り、`images/` の画像を添付すればよい。

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
