"""CLI の複数動画対応(`run` コマンド)に関する単体テスト。

パイプライン自体の正しさは tests/test_pipeline_e2e.py で検証済みのため、
ここでは run_pipeline を差し替え、CLI 自身のループ・オプションの使い回し・
失敗時の継続・出力先ディレクトリの決め方だけを確認する。
"""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from seminar_report import cli as cli_module
from seminar_report.cli import app
from seminar_report.config import get_settings
from seminar_report.models import Report
from seminar_report.pipeline import PipelineOptions, PipelineResult

runner = CliRunner()


def _touch_video(path: Path) -> Path:
    path.write_bytes(b"")
    return path


def _fake_result(video: Path, output_dir: Path) -> PipelineResult:
    report = Report(title=f"{video.stem} のレポート", source_video=video.name)
    return PipelineResult(
        report=report,
        transcript=None,  # type: ignore[arg-type]
        output_dir=output_dir,
        markdown_path=output_dir / "report.md",
        storage_path=output_dir / "report.confluence.xml",
        html_path=output_dir / "report.html",
        report_json_path=output_dir / "report.json",
        transcript_path=output_dir / "transcript.json",
    )


def test_run_reuses_shared_options_across_videos(monkeypatch, tmp_path: Path) -> None:
    video_a = _touch_video(tmp_path / "a.mp4")
    video_b = _touch_video(tmp_path / "b.mp4")
    out_dir = tmp_path / "out"

    calls: list[tuple[Path, Path, PipelineOptions]] = []

    def fake_run_pipeline(video, output_dir, options, on_progress=None):
        calls.append((video, output_dir, options))
        return _fake_result(video, output_dir)

    monkeypatch.setattr(cli_module, "run_pipeline", fake_run_pipeline)
    monkeypatch.setattr(cli_module, "export_zip", lambda output_dir, report: output_dir / "report.zip")

    result = runner.invoke(
        app,
        ["run", str(video_a), str(video_b), "--out", str(out_dir), "--detail", "brief"],
    )

    assert result.exit_code == 0, result.output
    assert [video for video, _, _ in calls] == [video_a, video_b]
    assert [output_dir for _, output_dir, _ in calls] == [out_dir / "a", out_dir / "b"]

    # 切り出し範囲・詳細度などの生成条件は動画ごとに変える理由が無いため、
    # 同一の PipelineOptions を使い回す。
    options_a, options_b = calls[0][2], calls[1][2]
    assert options_a is options_b

    assert "完了: 2/2 件成功" in result.output


def test_run_continues_after_one_video_fails(monkeypatch, tmp_path: Path) -> None:
    video_a = _touch_video(tmp_path / "a.mp4")
    video_b = _touch_video(tmp_path / "b.mp4")

    calls: list[Path] = []

    def fake_run_pipeline(video, output_dir, options, on_progress=None):
        calls.append(video)
        if video == video_a:
            raise RuntimeError("文字起こしに失敗しました")
        return _fake_result(video, output_dir)

    monkeypatch.setattr(cli_module, "run_pipeline", fake_run_pipeline)
    monkeypatch.setattr(cli_module, "export_zip", lambda output_dir, report: output_dir / "report.zip")

    result = runner.invoke(app, ["run", str(video_a), str(video_b), "--out", str(tmp_path / "out")])

    # 1本失敗しても2本目は処理される
    assert calls == [video_a, video_b]
    assert "✗ 失敗" in result.output
    assert "完了: 1/2 件成功" in result.output
    # 一部失敗はゼロ以外の終了コードで報告する
    assert result.exit_code != 0


def test_run_single_video_with_out_uses_out_directly(monkeypatch, tmp_path: Path) -> None:
    """単一動画時は --out をそのまま出力先にする(既存動作との互換)。"""
    video = _touch_video(tmp_path / "a.mp4")
    out_dir = tmp_path / "custom-out"

    calls: list[Path] = []

    def fake_run_pipeline(v, output_dir, options, on_progress=None):
        calls.append(output_dir)
        return _fake_result(v, output_dir)

    monkeypatch.setattr(cli_module, "run_pipeline", fake_run_pipeline)
    monkeypatch.setattr(cli_module, "export_zip", lambda output_dir, report: output_dir / "report.zip")

    result = runner.invoke(app, ["run", str(video), "--out", str(out_dir)])

    assert result.exit_code == 0, result.output
    assert calls == [out_dir]


def test_run_single_video_without_out_uses_settings_output_dir(monkeypatch, tmp_path: Path) -> None:
    """--out 未指定時は settings.output_dir/動画名 を使う(既存動作との互換)。"""
    video = _touch_video(tmp_path / "a.mp4")
    settings = get_settings()
    monkeypatch.setattr(settings, "output_dir", tmp_path / "default-out")

    calls: list[Path] = []

    def fake_run_pipeline(v, output_dir, options, on_progress=None):
        calls.append(output_dir)
        return _fake_result(v, output_dir)

    monkeypatch.setattr(cli_module, "run_pipeline", fake_run_pipeline)
    monkeypatch.setattr(cli_module, "export_zip", lambda output_dir, report: output_dir / "report.zip")

    result = runner.invoke(app, ["run", str(video)])

    assert result.exit_code == 0, result.output
    assert calls == [tmp_path / "default-out" / "a"]
