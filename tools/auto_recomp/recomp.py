#!/usr/bin/env python3
"""Universal Xbox XBE recompilation workflow: one command, no hooks.

    python sdk/tools/auto_recomp/recomp.py --source <huggingface link | .iso | default.xbe | URL>

Give it a Hugging Face link, an ISO image or a default.xbe (local path or
URL) and it will, without asking for a single hook or manual config edit:

  1. fetch and, if needed, read the disc image to pull out default.xbe (fetch.py)
  2. read the XBE header to name the game and find its XDK build (xbe_info.py)
  3. scaffold a game project from sdk/templates/game, with every placeholder
     the manual "Game Setup" README steps ask for filled in automatically
     (scaffold.py) - app::kGame stays exactly as hook-free as the template:
     no [[hook]], no [[function]], no X86_FUNC is ever written by this tool
  4. run xbe_symbols to match the XBE against the SDK's XDK signatures and
     write config/game.toml - the same auto-discovery build.bat performs
  5. build the game with CMake + Ninja + Clang, on Windows only (step 1-4
     need no compiler and work on any OS)
  6. split the result into two folders: the compiled executable, and the
     recompiled C++ source plus the scaffolded project - ready to zip, ship
     or open in an IDE

Run it again on the same --games-root/--slug after the title's XBE or the
SDK's signatures change: every step is idempotent and only touches the files
it owns.
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import fetch  # noqa: E402
import scaffold  # noqa: E402
import toolchain  # noqa: E402
import xbe_info  # noqa: E402

SDK_ROOT = Path(__file__).resolve().parents[2]


def log(msg: str) -> None:
    print(f"[auto_recomp] {msg}", flush=True)


def run(cmd, cwd, allow_fail=False) -> subprocess.CompletedProcess:
    log("$ " + " ".join(str(c) for c in cmd))
    result = subprocess.run(cmd, cwd=cwd)
    if result.returncode != 0 and not allow_fail:
        raise SystemExit(f"command failed ({result.returncode}): {' '.join(str(c) for c in cmd)}")
    return result


def write_config(project_dir: Path, xbe_path: Path, exe_name: str) -> bool:
    """Runs xbe_symbols the same way build.bat's first build does. Returns
    True if config/game.toml now holds real symbols (vs. the template's
    placeholder), i.e. whether a full build can proceed."""
    out_dir = project_dir / "out" / "build" / "release"
    config_path = project_dir / "config" / "game.toml"
    if config_path.exists() and "[symbols]" in config_path.read_text(errors="ignore"):
        log("config/game.toml already has symbols, keeping it (delete it to re-run xbe_symbols)")
        return True

    run(["cmake", "--preset", "release"], cwd=project_dir)
    sdk_dir_txt = out_dir / "sdk_dir.txt"
    if not sdk_dir_txt.exists():
        raise SystemExit("out/build/release/sdk_dir.txt was not written; update the SDK")
    sdk_dir = sdk_dir_txt.read_text().strip()

    run(["cmake", "--build", str(out_dir), "--target", "xbe_symbols"], cwd=project_dir)
    symbols_exe = out_dir / "xbe_symbols.exe"
    if not symbols_exe.exists():
        symbols_exe = out_dir / "xbe_symbols"  # just in case of a non-Windows experimental build

    config_path.parent.mkdir(parents=True, exist_ok=True)
    result = run([
        str(symbols_exe), str(xbe_path), str(Path(sdk_dir) / "xdk"),
        "--library", f"{exe_name}.dll", "--out", str(config_path),
    ], cwd=project_dir, allow_fail=True)
    if result.returncode != 0:
        log("xbe_symbols could not resolve this XBE's library build against the SDK's signatures.")
        log("This usually means the game ships an XDK build the SDK has no xdk/<build>/signatures.toml for yet -")
        log("see sdk/README.md ('A new XDK build needs...') for how those are produced from a finished title.")
        return False
    return True


def build_project(project_dir: Path) -> Path:
    out_dir = project_dir / "out" / "build" / "release"
    run(["cmake", "--build", str(out_dir)], cwd=project_dir)
    return out_dir


def package(project_dir: Path, out_dir: Path, exe_name: str, slug: str, dist_root: Path) -> dict:
    exe_dist = dist_root / slug / "exe"
    src_dist = dist_root / slug / "source"
    if exe_dist.exists():
        shutil.rmtree(exe_dist)
    if src_dist.exists():
        shutil.rmtree(src_dist)
    exe_dist.mkdir(parents=True)
    src_dist.mkdir(parents=True)

    # 1. the recompilation, as an exe: the executable plus the game code dll
    #    it delay loads - no game assets, same policy as release.bat.
    exe_file = out_dir / f"{exe_name}.exe"
    dll_file = out_dir / f"{exe_name}.dll"
    if not exe_file.exists():
        raise SystemExit(f"build finished without producing {exe_file}")
    shutil.copyfile(exe_file, exe_dist / exe_file.name)
    if dll_file.exists():
        shutil.copyfile(dll_file, exe_dist / dll_file.name)
    redist_dir = toolchain.find_vc_redist_dir()
    if redist_dir:
        for name in ("vcruntime140.dll", "vcruntime140_1.dll", "msvcp140.dll"):
            src = redist_dir / name
            if src.exists():
                shutil.copyfile(src, exe_dist / name)
    (exe_dist / "assets").mkdir(exist_ok=True)
    (exe_dist / "README.txt").write_text(
        f"{exe_name}\n{'=' * len(exe_name)}\n\n"
        "Put your own legally acquired copy of the game's files (or its .iso) in the\n"
        "assets folder beside this executable, then run it.\n"
        "Needs the Visual C++ runtime (vcruntime140.dll, msvcp140.dll); install the\n"
        "'Visual C++ Redistributable for Visual Studio 2015-2022' if it won't start.\n"
    )

    # 2. the source: the scaffolded project (build scripts, config, hook-free
    #    app::kGame) plus generated/, the C++ the recompiler produced from the XBE.
    def ignore(dirpath, names):
        skip = {"out", "build", "assets", ".vs", ".vscode", ".idea"}
        return [n for n in names if n in skip]

    shutil.copytree(project_dir, src_dist / project_dir.name, ignore=ignore)

    exe_zip = dist_root / slug / f"{exe_name}.zip"
    src_zip = dist_root / slug / f"{exe_name}-source.zip"
    _zip_dir(exe_dist, exe_zip)
    _zip_dir(src_dist, src_zip)
    return {"exe_dir": exe_dist, "source_dir": src_dist, "exe_zip": exe_zip, "source_zip": src_zip}


def _zip_dir(root: Path, zip_path: Path) -> None:
    if zip_path.exists():
        zip_path.unlink()
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in root.rglob("*"):
            if p.is_file():
                zf.write(p, p.relative_to(root.parent))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source", required=True,
                     help="Hugging Face link, .iso/.xiso, default.xbe, a direct URL to one, or a .zip with one inside")
    ap.add_argument("--title", help="override the window title (default: read from the XBE's certificate)")
    ap.add_argument("--exe", help="override the executable name (default: derived from the title)")
    ap.add_argument("--slug", help="override the project folder name under --games-root")
    ap.add_argument("--games-root", default=str(SDK_ROOT / "games"),
                     help="where the scaffolded game project is created (default: sdk/games)")
    ap.add_argument("--out", default=str(SDK_ROOT / "dist"),
                     help="where the final exe/ and source/ folders (and their zips) are written")
    ap.add_argument("--skip-build", action="store_true", help="stop after scaffolding + config, don't compile")
    args = ap.parse_args(argv)

    workdir = Path(args.games_root).resolve() / "_fetch"
    log(f"resolving source: {args.source}")
    ingested = fetch.ingest(args.source, workdir)
    log(f"found default.xbe: {ingested.xbe_path}" + (f" (from {ingested.disc_path.name})" if ingested.disc_path else ""))

    info = xbe_info.parse_xbe(ingested.xbe_path)
    title = args.title or info.title or "Game"
    slug = args.slug or xbe_info.slugify(title)
    exe_name = args.exe or scaffold.default_exe_name(title)
    log(f"title: {title!r}  title id: {info.title_id_hex}  debug build: {info.debug}  slug: {slug}")
    if info.libraries:
        log("libraries: " + ", ".join(f"{l.name} {l.major}.{l.minor}.{l.build}" for l in info.libraries))

    games_root = Path(args.games_root).resolve()
    project_dir = scaffold.create_game_project(SDK_ROOT, games_root, slug, info, args.title, exe_name, args.source)
    log(f"project scaffolded at {project_dir} (no hooks written)")

    assets_dir = project_dir / "assets"
    shutil.copyfile(ingested.xbe_path, assets_dir / "default.xbe")
    if ingested.disc_path is not None:
        disc_dest = assets_dir / ingested.disc_path.name
        if ingested.disc_path.resolve() != disc_dest.resolve():
            shutil.copyfile(ingested.disc_path, disc_dest)
        log(f"copied the disc image into assets/ too, for playing the game once it's built")

    if not toolchain.is_windows():
        log("not running on Windows: stopping after scaffolding. The project at")
        log(f"  {project_dir}")
        log("is ready; run build.bat on Windows (or the sdk's auto_recomp GitHub Actions workflow) to finish.")
        return 0

    if not toolchain.ensure_toolchain_on_path():
        missing = toolchain.missing_tools()
        log("missing build tools: " + ", ".join(missing) if missing else "toolchain not found")
        log("install Visual Studio 2022 with 'Desktop development with C++', 'C++ Clang tools for "
            "Windows' and 'C++ CMake tools for Windows' (or run build.bat once, which offers to add them), "
            "then run this again.")
        return 1

    has_symbols = write_config(project_dir, ingested.xbe_path, exe_name)
    if not has_symbols:
        return 2
    if args.skip_build:
        log("--skip-build set, stopping with config/game.toml written")
        return 0

    log("building (first build recompiles the game's code, this can take 30-90 minutes)...")
    out_dir = build_project(project_dir)

    result = package(project_dir, out_dir, exe_name, slug, Path(args.out).resolve())
    log("done:")
    log(f"  recompiled exe   : {result['exe_dir']}  ({result['exe_zip']})")
    log(f"  source + generated code : {result['source_dir']}  ({result['source_zip']})")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (fetch.IngestError, ValueError) as e:
        log(f"error: {e}")
        raise SystemExit(1)
    except KeyboardInterrupt:
        raise SystemExit(130)
