"""CUDA ライブラリの探索と、GPU 判定の失敗理由の扱い。

Windows で `nvidia-cublas-cu12` を入れても、DLL 検索パスに登録しないと
CTranslate2 から見えず `cublas64_12.dll is not found` で落ちる。その登録処理と、
「GPU 無し」と「判定失敗」を区別する経路を検証する。
"""

from __future__ import annotations

import builtins
import sys
from pathlib import Path

import pytest

from seminar_report.transcribe import cuda as cuda_module
from seminar_report.transcribe import whisper as whisper_module


@pytest.fixture(autouse=True)
def _reset_cache():
    """ensure_cuda_libs() はプロセス内で結果を使い回すため毎回クリアする。"""
    cuda_module._status = None
    cuda_module._handles.clear()
    yield
    cuda_module._status = None
    cuda_module._handles.clear()


def _fake_nvidia_tree(root: Path, suffix: str) -> Path:
    """pip が作る site-packages/nvidia/<lib>/<bin|lib>/ を模す。"""
    subdir = "bin" if suffix == ".dll" else "lib"
    for lib, filename in (("cublas", "cublas64_12"), ("cudnn", "cudnn64_9")):
        d = root / lib / subdir
        d.mkdir(parents=True)
        (d / f"{filename}{suffix}").write_bytes(b"")
    return root


def test_finds_installed_cuda_libraries(tmp_path, monkeypatch) -> None:
    root = _fake_nvidia_tree(tmp_path / "nvidia", ".dll")
    monkeypatch.setattr(cuda_module, "_nvidia_roots", lambda: [root])

    status = cuda_module.ensure_cuda_libs()

    assert status.installed is True
    assert status.has_cublas is True
    assert status.has_cudnn is True


def test_reports_not_installed_when_absent(monkeypatch) -> None:
    monkeypatch.setattr(cuda_module, "_nvidia_roots", lambda: [])

    status = cuda_module.ensure_cuda_libs()

    assert status.installed is False
    assert status.found_libs == []


def test_registers_dll_directories_when_available(tmp_path, monkeypatch) -> None:
    """Windows 相当の環境で add_dll_directory が呼ばれること。"""
    root = _fake_nvidia_tree(tmp_path / "nvidia", ".dll")
    monkeypatch.setattr(cuda_module, "_nvidia_roots", lambda: [root])

    called: list[str] = []
    monkeypatch.setattr(
        cuda_module.os, "add_dll_directory", lambda p: called.append(p) or object(),
        raising=False,
    )

    status = cuda_module.ensure_cuda_libs()

    assert len(called) == 2
    assert len(status.registered) == 2
    # 登録ハンドルを捨てると登録が外れるため、保持し続ける必要がある。
    assert len(cuda_module._handles) == 2


def test_linux_shared_objects_are_discovered(tmp_path, monkeypatch) -> None:
    """Linux では .so を探す(登録自体は不要)。"""
    root = _fake_nvidia_tree(tmp_path / "nvidia", ".so")
    monkeypatch.setattr(cuda_module, "_nvidia_roots", lambda: [root])
    monkeypatch.delattr(cuda_module.os, "add_dll_directory", raising=False)

    status = cuda_module.ensure_cuda_libs()

    assert status.has_cublas is True
    assert status.registered == []


def test_result_is_cached(tmp_path, monkeypatch) -> None:
    """import のたびに走らせないよう結果を使い回す。"""
    root = _fake_nvidia_tree(tmp_path / "nvidia", ".dll")
    calls = []

    def roots():
        calls.append(1)
        return [root]

    monkeypatch.setattr(cuda_module, "_nvidia_roots", roots)

    first = cuda_module.ensure_cuda_libs()
    second = cuda_module.ensure_cuda_libs()

    assert first is second
    assert len(calls) <= 2  # 1 回の実行内で installed 判定と列挙に使う


def test_cuda_probe_reports_failure_reason(monkeypatch) -> None:
    """「GPU 無し」と「判定できなかった」を区別できること。"""
    real_import = builtins.__import__

    def boom(name, *args, **kwargs):
        if name == "ctranslate2":
            raise RuntimeError("Library cublas64_12.dll is not found")
        return real_import(name, *args, **kwargs)

    monkeypatch.setitem(sys.modules, "ctranslate2", None)
    monkeypatch.setattr("builtins.__import__", boom)

    count, error = whisper_module._cuda_probe()

    assert count == 0
    assert error is not None
    assert "cublas64_12.dll" in error


def test_cuda_probe_returns_no_error_when_gpu_absent(monkeypatch) -> None:
    """正常に 0 台と判定できた場合は理由を付けない。"""

    class FakeCT:
        @staticmethod
        def get_cuda_device_count():
            return 0

    monkeypatch.setitem(sys.modules, "ctranslate2", FakeCT)

    count, error = whisper_module._cuda_probe()

    assert count == 0
    assert error is None
