"""pip で入れた CUDA ライブラリを CTranslate2 から見えるようにする。

Windows の Python 3.8 以降、拡張モジュールが依存する DLL は PATH からは
探索されない。`nvidia-cublas-cu12` などを pip で入れても DLL は
`site-packages/nvidia/<lib>/bin/` に置かれるだけで、CTranslate2 の読み込み時に
`Library cublas64_12.dll is not found or cannot be loaded` で失敗する。
faster-whisper 側はこの登録を行わないため、ここで `os.add_dll_directory()` を
呼んでおく。

Linux では `.so` が `nvidia/<lib>/lib/` に入り、こちらは通常 RPATH で解決される
ため何もしない。
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

# add_dll_directory() の戻り値は close() されると登録が外れる。プロセスが
# 生きている間は保持し続ける必要があるため、モジュール変数に握っておく。
_handles: list[object] = []
_status: CudaLibStatus | None = None


@dataclass
class CudaLibStatus:
    """CUDA ライブラリの探索結果。doctor での表示に使う。"""

    installed: bool = False
    """nvidia-* パッケージ(名前空間パッケージ `nvidia`)が入っているか。"""

    registered: list[Path] = field(default_factory=list)
    """DLL 検索パスに登録できたディレクトリ。"""

    found_libs: list[str] = field(default_factory=list)
    """実際に見つかった主要ライブラリのファイル名。"""

    error: str | None = None

    @property
    def has_cublas(self) -> bool:
        return any("cublas" in name for name in self.found_libs)

    @property
    def has_cudnn(self) -> bool:
        return any("cudnn" in name for name in self.found_libs)


def _nvidia_roots() -> list[Path]:
    """`nvidia` 名前空間パッケージの実体ディレクトリ。"""
    try:
        import nvidia
    except ImportError:
        return []
    return [Path(p) for p in getattr(nvidia, "__path__", [])]


def _lib_dirs() -> list[Path]:
    """CUDA ライブラリが置かれているディレクトリを列挙する。

    Windows は `nvidia/cublas/bin`、Linux は `nvidia/cublas/lib` に入る。
    """
    dirs: list[Path] = []
    for root in _nvidia_roots():
        if not root.is_dir():
            continue
        for package in sorted(root.iterdir()):
            for name in ("bin", "lib"):
                candidate = package / name
                if candidate.is_dir():
                    dirs.append(candidate)
    return dirs


def ensure_cuda_libs() -> CudaLibStatus:
    """CUDA ライブラリを DLL 検索パスに登録する(Windows のみ実効)。

    CTranslate2 を import する前に呼ぶこと。複数回呼ばれても登録は一度だけ。
    """
    global _status
    if _status is not None:
        return _status

    status = CudaLibStatus()
    lib_dirs = _lib_dirs()
    status.installed = bool(_nvidia_roots())

    for directory in lib_dirs:
        try:
            names = [p.name for p in directory.iterdir() if p.suffix in (".dll", ".so")]
        except OSError:
            continue
        status.found_libs.extend(
            name for name in names if "cublas" in name or "cudnn" in name
        )

        # add_dll_directory は Windows にしか存在しない。
        if not hasattr(os, "add_dll_directory"):
            continue
        try:
            _handles.append(os.add_dll_directory(str(directory)))
            status.registered.append(directory)
        except OSError as exc:  # 実在しない・権限が無い等
            status.error = str(exc)

    # CTranslate2 の一部の経路は PATH も見るため、併せて通しておく。
    if sys.platform == "win32" and status.registered:
        joined = os.pathsep.join(str(d) for d in status.registered)
        os.environ["PATH"] = joined + os.pathsep + os.environ.get("PATH", "")

    _status = status
    return status
