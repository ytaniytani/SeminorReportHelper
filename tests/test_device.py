"""文字起こしのデバイス選択。

GPU があるのに CPU で動いてしまう不具合を二度と起こさないための回帰テスト。
faster-whisper は torch ではなく CTranslate2 で動くため、判定も CTranslate2
に対して行われていなければならない。
"""

from __future__ import annotations

import sys
import types

import pytest

from seminar_report.transcribe import whisper as whisper_module


@pytest.fixture
def fake_ctranslate2(monkeypatch):
    """ctranslate2 を差し替えて CUDA デバイス数を任意に見せる。"""

    def install(count: int) -> None:
        module = types.SimpleNamespace(get_cuda_device_count=lambda: count)
        monkeypatch.setitem(sys.modules, "ctranslate2", module)

    return install


def test_gpu_is_used_when_cuda_is_available(fake_ctranslate2) -> None:
    fake_ctranslate2(1)
    assert whisper_module._resolve_device("auto") == ("cuda", "float16")


def test_cpu_is_used_when_no_cuda_device(fake_ctranslate2) -> None:
    fake_ctranslate2(0)
    assert whisper_module._resolve_device("auto") == ("cpu", "int8")


def test_detection_does_not_depend_on_torch(monkeypatch, fake_ctranslate2) -> None:
    """torch が入っていなくても GPU を検出できること。

    以前は `import torch` の成否で判定しており、torch を入れていない
    (そして faster-whisper には不要な) 環境では GPU が必ず無視されていた。
    """
    fake_ctranslate2(2)
    monkeypatch.setitem(sys.modules, "torch", None)
    assert whisper_module.cuda_device_count() == 2
    assert whisper_module._resolve_device("auto")[0] == "cuda"


def test_missing_ctranslate2_falls_back_to_cpu(monkeypatch) -> None:
    """ctranslate2 を読めない環境でも例外にせず CPU として扱う。"""
    monkeypatch.setitem(sys.modules, "ctranslate2", None)

    assert whisper_module.cuda_device_count() == 0
    assert whisper_module._resolve_device("auto") == ("cpu", "int8")


def test_cuda_probe_error_falls_back_to_cpu(fake_ctranslate2, monkeypatch) -> None:
    """デバイス数の問い合わせ自体が失敗しても落ちないこと。"""

    def explode():
        raise RuntimeError("ドライバの初期化に失敗しました")

    monkeypatch.setitem(
        sys.modules, "ctranslate2", types.SimpleNamespace(get_cuda_device_count=explode)
    )
    assert whisper_module.cuda_device_count() == 0


def test_explicit_device_is_respected(fake_ctranslate2) -> None:
    fake_ctranslate2(1)
    assert whisper_module._resolve_device("cpu") == ("cpu", "int8")


class _StubModel:
    """WhisperModel の代役。cuda では失敗し cpu では成功する。"""

    instances: list[dict] = []

    def __init__(self, size, device, compute_type, cpu_threads):
        _StubModel.instances.append(
            {
                "size": size,
                "device": device,
                "compute_type": compute_type,
                "cpu_threads": cpu_threads,
            }
        )
        if device == "cuda":
            raise RuntimeError("cuDNN が見つかりません")


@pytest.fixture
def stub_whisper(monkeypatch):
    _StubModel.instances = []
    module = types.SimpleNamespace(WhisperModel=_StubModel)
    monkeypatch.setitem(sys.modules, "faster_whisper", module)
    return _StubModel


def test_cuda_failure_falls_back_to_cpu(stub_whisper) -> None:
    """CUDA が見えても cuBLAS/cuDNN が無ければ CPU で続行する。

    ここで例外を通すと、GPU 判定を直したことでかえって動かなくなる。
    """
    model, device, error = whisper_module._load_model("medium", "cuda", "float16", 8)

    assert device == "cpu"
    assert error and "cuDNN" in error
    assert [i["device"] for i in stub_whisper.instances] == ["cuda", "cpu"]
    assert stub_whisper.instances[-1]["compute_type"] == "int8"


def test_cpu_failure_is_not_swallowed(stub_whisper, monkeypatch) -> None:
    """CPU で失敗した場合は握りつぶさずに伝える(縮退先が無いため)。"""

    def always_fail(size, device, compute_type, cpu_threads):
        raise RuntimeError("モデルが壊れています")

    monkeypatch.setitem(
        sys.modules, "faster_whisper", types.SimpleNamespace(WhisperModel=always_fail)
    )
    with pytest.raises(RuntimeError, match="モデルが壊れています"):
        whisper_module._load_model("medium", "cpu", "int8", 4)


def test_cpu_threads_are_passed_through(stub_whisper) -> None:
    """既定の 4 スレッド固定では多コア機を使い切れないため、明示的に渡す。"""
    whisper_module._load_model("small", "cpu", "int8", 16)
    assert stub_whisper.instances[-1]["cpu_threads"] == 16


def test_beam_size_is_lower_on_cpu_than_gpu(monkeypatch) -> None:
    """CPU では beam_size が実行時間に直結するので既定を下げる。"""
    settings = whisper_module.get_settings()
    monkeypatch.setattr(settings, "whisper_beam_size", None)

    assert whisper_module._resolve_beam_size("cpu") == 1
    assert whisper_module._resolve_beam_size("cuda") == 5


def test_configured_beam_size_wins(monkeypatch) -> None:
    settings = whisper_module.get_settings()
    monkeypatch.setattr(settings, "whisper_beam_size", 3)

    assert whisper_module._resolve_beam_size("cpu") == 3
    assert whisper_module._resolve_beam_size("cuda") == 3


def test_cpu_threads_default_to_core_count(monkeypatch) -> None:
    settings = whisper_module.get_settings()
    monkeypatch.setattr(settings, "whisper_cpu_threads", None)
    monkeypatch.setattr(whisper_module.os, "cpu_count", lambda: 12)

    assert whisper_module._resolve_cpu_threads() == 12


def test_configured_cpu_threads_win(monkeypatch) -> None:
    settings = whisper_module.get_settings()
    monkeypatch.setattr(settings, "whisper_cpu_threads", 2)

    assert whisper_module._resolve_cpu_threads() == 2
