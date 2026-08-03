# Claude Code設定

## 言語設定

**すべてのやり取りを日本語で行う**

- ユーザーへの説明・質問・フィードバック → 日本語
- コミットメッセージ → 日本語
- コード内のコメント → 日本語（説明が必要な場合）
- README / ドキュメント → 日本語

## プロジェクト概要

セミナー録画（.mp4）から、画像付きの日本語レポートを生成し Confluence に反映するツール。

- 文字起こし：faster-whisper（ローカル完結、GPU対応）
- レポート生成：Claude / OpenAI / Ollama
- フレーム抽出：ffmpeg + numpy
- Web UI：FastAPI + HTML/JS

## 開発ガイドライン

- GPU対応が重要：Whisper の処理時間が全体の 80～95% を占める
- 30分動画の目安：GPU あれば 3～6分、CPU なら 20～40分
- テストは結合テスト（`test_pipeline_e2e.py`, `test_web.py`）で全体フロー検証
- Windows環境での動作確認を重視

## 重要なファイル

| パス | 役割 |
|---|---|
| `seminar_report/pipeline.py` | 全体のオーケストレーション |
| `seminar_report/transcribe/whisper.py` | faster-whisper ラッパー（GPU判定・CUDA フォールバック） |
| `seminar_report/report/generator.py` | LLM呼び出しの並列化、レポート生成 |
| `seminar_report/web/app.py` | FastAPI、SSE、ジョブ管理 |
| `seminar_report/web/static/app.js` | Web UI（SSE+ポーリング、localStorage） |
| `seminar_report/cli.py` | CLI コマンド（`doctor` 診断など） |
| `README.md` | ユーザー向けドキュメント |

## 最近の改善（2026年8月）

### GPU対応の修正

- **根本原因**：`import torch` が失敗していたため、NVIDIA GPU があっても CPU で動作していた
- **対処**：`ctranslate2.get_cuda_device_count()` で直接判定
- **フォールバック**：CUDA デバイスが見えても cuDNN/cuBLAS が無い場合は CPU にフォールバック

### 長時間ジョブの安定化

- **SSE ハートビート**：15秒ごとに `keepalive` を送信（モデルDL中の接続切断を防止）
- **ポーリング fallback**：SSE が死んでも 3秒間隔で `GET /api/jobs/{id}` を続行
- **localStorage**：ページをリロードしてもジョブ ID が保持される
- **ジョブ一覧**：`GET /api/jobs` で復帰可能なジョブを検索できる

### LLM 並列化

- **summarize_chunks（map 段）**：区間ごとの要約を ThreadPoolExecutor で並列実行
- **write_sections（write 段）**：複数セクションを同時に執筆（順序は outline で決定済み）
- **キャプチャ予算の事後切り詰め**：各セクションに先に配分→完了後に全体チェック

### 診断機能

`seminar-report doctor` コマンドで環境をチェック：

```
ffmpeg          : /usr/bin/ffmpeg
CUDA デバイス   : 1 台
使用デバイス    : cuda / float16
CPU コア数      : 8
Whisper モデル  : medium
LLM             : claude / claude-sonnet-5
```

## テスト実行

```bash
uv run pytest                 # 全テスト（60秒程度）
uv run pytest -xvs            # 詳細出力
uv run pytest tests/test_web.py  # Web API のみ
```

初回実行時は ffmpeg で 70秒のテスト動画を合成するため数十秒かかります。

## Windows での実行

```bash
# Python が正しくインストールされていること
python --version              # Python 3.10+ が表示されること

# CUDA ライブラリをインストール（GPU がある場合）
pip install nvidia-cublas-cu12 nvidia-cudnn-cu12

# セットアップ
uv venv
uv pip install -e ".[asr,llm,dev]"

# 環境診断
uv run seminar-report doctor

# Web UI 起動
uv run seminar-report serve   # http://127.0.0.1:8000
```

Microsoft Store の Python が邪魔をする場合は、スタート → "実行" → `ms-settings:appsfeatures-app-executionaliases` で
「python」と「python3」のエイリアスを無効化してください。
