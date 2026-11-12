"""Finds clang++, cmake and ninja the same way build.bat does (Visual Studio's
bundled Clang/CMake/Ninja components), so the Python pipeline works on a
plain Windows + Visual Studio 2022 install without the user opening a
Developer Prompt first. On any other OS, building is skipped; everything up
to and including the generated C++ source still comes out, since that part
needs no compiler at all.
"""
from __future__ import annotations

import os
import platform
import shutil
import subprocess
from pathlib import Path
from typing import Optional


def is_windows() -> bool:
    return platform.system() == "Windows"


def _vswhere() -> Optional[Path]:
    pf86 = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
    p = Path(pf86) / "Microsoft Visual Studio" / "Installer" / "vswhere.exe"
    return p if p.exists() else None


def ensure_toolchain_on_path() -> bool:
    """Best-effort: makes clang++, cmake and ninja resolvable via PATH for
    this process and its subprocesses. Returns True once all three are
    found (already on PATH, or located through a Visual Studio install)."""
    if shutil.which("clang++") and shutil.which("cmake") and shutil.which("ninja"):
        return True
    if not is_windows():
        return False

    vswhere = _vswhere()
    if not vswhere:
        return False
    try:
        out = subprocess.run(
            [str(vswhere), "-latest", "-products", "*",
             "-requires", "Microsoft.VisualStudio.Component.VC.Tools.x86.x64",
             "-property", "installationPath"],
            capture_output=True, text=True, timeout=30, check=False,
        )
    except OSError:
        return False
    vsdir = out.stdout.strip().splitlines()[0] if out.stdout.strip() else None
    if not vsdir:
        return False

    vsdir_p = Path(vsdir)
    extra = [
        vsdir_p / "Common7" / "IDE" / "CommonExtensions" / "Microsoft" / "CMake" / "CMake" / "bin",
        vsdir_p / "Common7" / "IDE" / "CommonExtensions" / "Microsoft" / "CMake" / "Ninja",
        vsdir_p / "VC" / "Tools" / "Llvm" / "x64" / "bin",
    ]
    llvm_pf = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "LLVM" / "bin"
    if (llvm_pf / "clang++.exe").exists():
        extra.insert(0, llvm_pf)

    os.environ["PATH"] = os.pathsep.join(str(p) for p in extra) + os.pathsep + os.environ.get("PATH", "")
    return bool(shutil.which("clang++") and shutil.which("cmake") and shutil.which("ninja"))


def missing_tools() -> list:
    return [t for t in ("clang++", "cmake", "ninja", "git") if not shutil.which(t)]


def find_vc_redist_dir() -> Optional[Path]:
    """Folder holding vcruntime140.dll etc., the same one release.bat copies
    beside the exe so a fresh Windows install needs nothing more."""
    vswhere = _vswhere()
    if not vswhere:
        return None
    try:
        out = subprocess.run(
            [str(vswhere), "-latest", "-products", "*", "-property", "installationPath"],
            capture_output=True, text=True, timeout=30, check=False,
        )
    except OSError:
        return None
    vsdir = out.stdout.strip().splitlines()[0] if out.stdout.strip() else None
    if not vsdir:
        return None
    redist_root = Path(vsdir) / "VC" / "Redist" / "MSVC"
    if not redist_root.exists():
        return None
    for version_dir in sorted(redist_root.glob("1*"), reverse=True):
        for crt_dir in version_dir.glob("x64/Microsoft.VC*.CRT"):
            if (crt_dir / "vcruntime140.dll").exists():
                return crt_dir
    return None
