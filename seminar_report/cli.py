"""コマンドラインインターフェース。Web UI と同じ pipeline を呼ぶ薄い層。"""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console

from seminar_report.config import get_settings
from seminar_report.models import STEP_LABELS, DetailLevel, JobStep, Report
from seminar_report.pipeline import PipelineOptions, export_zip, run_pipeline

app = typer.Typer(
    help="セミナー動画からレポートを生成し Confluence に反映します。",
    no_args_is_help=True,
)
console = Console()


def _progress_printer() -> callable:
    state = {"step": None}

    def report(step: JobStep, ratio: float, detail: str) -> None:
        label = STEP_LABELS.get(step, step.value)
        if state["step"] != step:
            state["step"] = step
            console.print(f"[bold cyan]▶[/] {label}")
        if detail:
            percent = f"{ratio * 100:5.1f}%" if ratio else "     "
            console.print(f"   {percent} {detail}", highlight=False)

    return report


@app.command()
def run(
    video: Path = typer.Argument(..., exists=True, dir_okay=False, help="入力の .mp4"),
    detail: DetailLevel = typer.Option(DetailLevel.STANDARD, "--detail", "-d", help="詳細度"),
    chars: int | None = typer.Option(None, "--chars", "-c", help="目標文字数(指定時は --detail より優先)"),
    max_captures: int | None = typer.Option(None, "--max-captures", help="画像の最大枚数"),
    provider: str | None = typer.Option(None, "--provider", "-p", help="claude | openai | ollama"),
    model: str | None = typer.Option(None, "--model", "-m", help="使用するモデル名(未指定なら .env の既定値)"),
    out: Path | None = typer.Option(None, "--out", "-o", help="出力先ディレクトリ"),
    whisper_model: str | None = typer.Option(None, "--whisper-model", help="tiny/base/small/medium/large-v3"),
    language: str | None = typer.Option(None, "--language", help="レポートの出力言語"),
    audio_language: str | None = typer.Option(None, "--audio-language", help="音声の言語(既定は自動判定)"),
    verify_captures: bool = typer.Option(False, "--verify-captures", help="Vision で画像を検証する(コスト増)"),
    crop: str | None = typer.Option(
        None,
        "--crop",
        help="キャプチャの切り出し矩形 left,top,right,bottom(0〜1の割合)。"
        "登壇者映像やロゴを除いてスライド部分だけを残したい場合に指定する",
    ),
    no_images: bool = typer.Option(False, "--no-images", help="画像を入れない"),
    no_cache: bool = typer.Option(False, "--no-cache", help="文字起こしキャッシュを使わない"),
    publish: bool = typer.Option(False, "--publish", help="Confluence に投稿する"),
    space: str | None = typer.Option(None, "--space", help="Confluence スペースキー"),
    title: str | None = typer.Option(None, "--title", help="Confluence ページタイトル"),
) -> None:
    """動画からレポートを生成する。"""
    from seminar_report.media.frames import parse_crop_box

    settings = get_settings()
    output_dir = out or settings.output_dir / video.stem

    capture_crop = None
    if crop:
        try:
            capture_crop = parse_crop_box(crop)
        except ValueError as exc:
            console.print(f"[red]--crop が不正です: {exc}[/]")
            raise typer.Exit(1) from exc

    options = PipelineOptions(
        detail=detail,
        target_chars=chars,
        max_captures=max_captures,
        provider=provider,
        model=model,
        language=language,
        whisper_model=whisper_model,
        whisper_language=audio_language,
        verify_captures=verify_captures,
        use_cache=not no_cache,
        include_images=not no_images,
        capture_crop=capture_crop,
    )

    result = run_pipeline(video, output_dir, options, _progress_printer())
    report = result.report

    included = sum(1 for c in report.captures if c.included)
    console.print()
    console.print(f"[bold green]✓[/] {report.title}")
    console.print(f"  本文 {report.body_char_count()} 字 / {len(report.sections)} セクション / 画像 {included} 枚")
    console.print(f"  出力: {result.output_dir}")

    zip_path = export_zip(output_dir, report)
    console.print(f"  ZIP : {zip_path}")

    if publish:
        _publish(report, space, title)


@app.command("publish")
def publish_command(
    report_dir: Path = typer.Argument(..., exists=True, file_okay=False, help="report.json のあるディレクトリ"),
    space: str | None = typer.Option(None, "--space", help="Confluence スペースキー"),
    title: str | None = typer.Option(None, "--title", help="ページタイトル"),
    parent_id: str | None = typer.Option(None, "--parent-id", help="親ページ ID"),
) -> None:
    """生成済みのレポートを Confluence に投稿する。"""
    report_path = report_dir / "report.json"
    if not report_path.exists():
        console.print(f"[red]report.json が見つかりません: {report_path}[/]")
        raise typer.Exit(1)
    report = Report.model_validate_json(report_path.read_text(encoding="utf-8"))
    _publish(report, space, title, parent_id)


def _publish(
    report: Report, space: str | None, title: str | None, parent_id: str | None = None
) -> None:
    from seminar_report.publish.confluence import ConfluenceError, publish_report

    try:
        result = publish_report(report, space_key=space, title=title, parent_id=parent_id)
    except ConfluenceError as exc:
        console.print(f"[red]投稿に失敗しました: {exc}[/]")
        raise typer.Exit(1) from exc
    console.print(f"[bold green]✓[/] Confluence に投稿しました (画像 {result.attached} 枚)")
    console.print(f"  {result.url}")


@app.command()
def transcribe_only(
    video: Path = typer.Argument(..., exists=True, dir_okay=False),
    whisper_model: str | None = typer.Option(None, "--whisper-model"),
    out: Path | None = typer.Option(None, "--out", "-o"),
) -> None:
    """文字起こしだけを行う(結果はキャッシュされる)。"""
    from seminar_report.transcribe.whisper import transcribe as run_transcribe

    printer = _progress_printer()
    transcript = run_transcribe(
        video,
        model_size=whisper_model,
        on_progress=lambda ratio, detail: printer(JobStep.TRANSCRIBE, ratio, detail),
    )
    dest = out or get_settings().output_dir / video.stem / "transcript.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(transcript.model_dump_json(indent=2), encoding="utf-8")
    console.print(f"[bold green]✓[/] {len(transcript.segments)} セグメント → {dest}")


@app.command()
def doctor() -> None:
    """実行環境を診断する(GPU が使えているかの確認に)。"""
    import os

    from seminar_report.media.audio import FFmpegError, ffmpeg_path
    from seminar_report.transcribe.cuda import ensure_cuda_libs
    from seminar_report.transcribe.whisper import (
        _cuda_probe,
        _resolve_beam_size,
        _resolve_cpu_threads,
        _resolve_device,
    )

    settings = get_settings()

    try:
        ffmpeg = ffmpeg_path()
    except FFmpegError as exc:
        ffmpeg = f"[red]見つかりません({exc})[/]"

    cuda_libs = ensure_cuda_libs()
    gpus, cuda_error = _cuda_probe()
    device, compute_type = _resolve_device(settings.whisper_device)

    console.print("[bold]実行環境[/]")
    console.print(f"  ffmpeg        : {ffmpeg}")
    console.print(f"  CPU コア数    : {os.cpu_count()}")

    if gpus > 0:
        console.print(f"  CUDA デバイス : [green]{gpus} 台[/]")
    elif cuda_error and "ctranslate2" in cuda_error:
        # 文字起こし自体が未導入。CUDA の問題ではないので誤解させない。
        console.print("  CUDA デバイス : [yellow]判定不可(faster-whisper 未インストール)[/]")
        console.print("                  [cyan]uv pip install -e \".[asr]\"[/] で導入できます")
    elif cuda_error:
        # 「GPU 非搭載」と「判定に失敗」は対処が違う。区別して出す。
        console.print("  CUDA デバイス : [red]判定に失敗しました[/]")
        console.print(f"                  {cuda_error}")
    else:
        console.print("  CUDA デバイス : [yellow]なし(CPU で動作します)[/]")

    # CUDA ライブラリの状態。cublas/cudnn は「入れたのに見えない」が起きやすい。
    console.print("  CUDA ライブラリ:", end=" ")
    if not cuda_libs.installed:
        console.print("[yellow]未インストール[/]")
    else:
        found = []
        found.append("cublas [green]✓[/]" if cuda_libs.has_cublas else "cublas [red]✗[/]")
        found.append("cudnn [green]✓[/]" if cuda_libs.has_cudnn else "cudnn [red]✗[/]")
        console.print(" / ".join(found))
        for directory in cuda_libs.registered:
            console.print(f"                  登録済み: {directory}")
        if not cuda_libs.registered and cuda_libs.found_libs:
            console.print("                  [dim](Windows 以外では登録不要)[/]")
        if cuda_libs.error:
            console.print(f"                  [red]{cuda_libs.error}[/]")

    console.print()
    console.print("[bold]文字起こし[/]")
    console.print(f"  モデル        : {settings.whisper_model}")
    console.print(f"  デバイス      : {device} / {compute_type}")
    console.print(f"  beam_size     : {_resolve_beam_size(device)}")
    if device == "cpu":
        console.print(f"  スレッド数    : {_resolve_cpu_threads()}")

    if gpus == 0:
        console.print()
        console.print("[yellow]GPU が検出されませんでした。[/]")
        console.print("  NVIDIA GPU 搭載機なら、次で CUDA ライブラリを入れると大幅に速くなります:")
        # `pip install` だと uv の .venv ではなく別の Python に入り、
        # 「入れたのに見つからない」状態になる。必ず uv 経由で案内する。
        console.print("    [cyan]uv pip install nvidia-cublas-cu12 nvidia-cudnn-cu12[/]")
        console.print("  [dim]※ `pip install` は別の Python に入るため効きません[/]")

    console.print()
    console.print("[bold]LLM[/]")
    keys = {
        "claude": settings.anthropic_api_key,
        "openai": settings.openai_api_key,
        "ollama": True,
    }
    models = {
        "claude": settings.claude_model,
        "openai": settings.openai_model,
        "ollama": settings.ollama_model,
    }
    current = settings.llm_provider
    console.print(f"  プロバイダ    : {current}")
    console.print(f"  モデル        : {models.get(current, '?')}")
    configured = "[green]設定済み[/]" if keys.get(current) else "[red]APIキー未設定[/]"
    console.print(f"  認証          : {configured}")
    console.print(f"  同時実行数    : {settings.llm_concurrency}")
    if current in ("claude", "openai"):
        console.print("  [dim]※ 従量課金のプロバイダです。無料で試すなら ollama も選べます[/]")

    console.print()
    console.print("[bold]Confluence[/]")
    if settings.confluence_configured():
        console.print(f"  [green]設定済み[/] ({settings.confluence_base_url})")
    else:
        console.print("  [yellow]未設定[/](ZIP 出力は利用できます)")


@app.command()
def serve(
    host: str = typer.Option("127.0.0.1", "--host"),
    port: int = typer.Option(8000, "--port"),
) -> None:
    """ローカル Web UI を起動する。"""
    import socket

    import uvicorn

    # bind してから URL を案内する。先に表示すると、衝突して起動できなかった場合に
    # 「別アプリが応答する URL」を開かせてしまい、原因が分からなくなる。
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        probe.bind((host, port))
    except OSError:
        console.print(f"[red]ポート {port} は既に他のアプリが使用しています。[/]")
        console.print(f"  別のポートを指定してください: [cyan]seminar-report serve --port {port + 1}[/]")
        if port == 8000:
            console.print("  ※ 8000 番は Epic Games Launcher などが使っていることがあります")
        raise typer.Exit(1)
    finally:
        probe.close()

    console.print(f"[bold cyan]▶[/] http://{host}:{port} を開いてください")
    uvicorn.run("seminar_report.web.app:app", host=host, port=port, log_level="info")


if __name__ == "__main__":
    app()
