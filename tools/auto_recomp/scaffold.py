"""Builds a game project from templates/game with everything the manual
"Game Setup" steps in the README ask for filled in automatically: title,
executable name and the already hook-free app::kGame and src/game/hooks.cpp
the template ships. This module only ever substitutes placeholder values in
those template files - it never adds a [[hook]], [[function]], [[switch]] or
an X86_FUNC body. Those stay something a human adds later for a specific
title that actually needs one; the workflow finds everything it can on its
own and leaves the rest as trapping stubs (xbe_symbols' normal behaviour for
a miss) rather than guessing.
"""
from __future__ import annotations

import re
import shutil
from pathlib import Path

from xbe_info import XbeInfo, slugify


def _patch_build_bat(text: str, title: str, xbe_file: str, exe_name: str) -> str:
    text = re.sub(r'set "GAME_TITLE=.*?"', f'set "GAME_TITLE={title}"', text, count=1)
    text = re.sub(r'set "GAME_XBE=.*?"', f'set "GAME_XBE={xbe_file}"', text, count=1)
    text = re.sub(r'set "GAME_EXE=.*?"', f'set "GAME_EXE={exe_name}"', text, count=1)
    return text


def _patch_release_bat(text: str, exe_name: str) -> str:
    return re.sub(r'set "GAME_EXE=.*?"', f'set "GAME_EXE={exe_name}"', text, count=1)


def _patch_cmakelists(text: str, exe_name: str) -> str:
    return re.sub(
        r"add_xbe_image\(\w+ default\.xbe game\.toml \$\{GAME_PARTITIONS\} game\)",
        f"add_xbe_image({exe_name} default.xbe game.toml ${{GAME_PARTITIONS}} game)",
        text,
        count=1,
    )


def _patch_game_info(text: str, title: str, short_title: str, name: str, log: str, exe_name: str) -> str:
    text = re.sub(r'(\.title = L)"[^"]*"', rf'\1"{title}"', text, count=1)
    text = re.sub(r'(\.short_title = L)"[^"]*"', rf'\1"{short_title}"', text, count=1)
    text = re.sub(r'(\.name = L)"[^"]*"', rf'\1"{name}"', text, count=1)
    text = re.sub(r'(\.log = )"[^"]*"', rf'\1"{log}"', text, count=1)
    text = re.sub(r'\{\{"default\.xbe", "[^"]*"\}\}', f'{{{{"default.xbe", "{exe_name}"}}}}', text, count=1)
    return text


def _patch_readme(text: str, title: str, origin: str) -> str:
    text = text.replace("Game Title", title)
    note = (
        f"\n> [!NOTE]\n> Scaffolded automatically by `sdk/tools/auto_recomp` from: `{origin}`. "
        "No hooks were written; only placeholder values in this template were filled in.\n"
    )
    lines = text.split("\n", 1)
    return lines[0] + "\n" + note + (lines[1] if len(lines) > 1 else "")


def default_exe_name(title: str) -> str:
    name = re.sub(r"[^A-Za-z0-9]", "", title.title())
    if not name:
        name = "Game"
    if not name[0].isalpha():
        name = "Game" + name
    return name


def create_game_project(sdk_root: Path, games_root: Path, slug: str, info: XbeInfo,
                         title_override: str | None, exe_override: str | None,
                         origin: str) -> Path:
    """Creates games_root/<slug> from templates/game, pre-filled for `info`.
    Returns the project directory. Safe to call again on the same slug: files
    this function doesn't own (assets/, a config/game.toml already written by
    xbe_symbols, anything added under src/game) are left alone.
    """
    template_dir = sdk_root / "templates" / "game"
    project_dir = games_root / slug
    project_dir.mkdir(parents=True, exist_ok=True)

    title = title_override or info.title or slug.replace("_", " ").title()
    exe_name = exe_override or default_exe_name(title)
    short_title = title if len(title) <= 24 else title[:21] + "..."
    window_name = re.sub(r"[^A-Za-z0-9]", "", title) or "Game"
    log_name = slugify(title, "game")

    # 1. copy the template verbatim, once (idempotent: never clobber a file a
    #    previous run or xbe_symbols already wrote - only config/game.toml's
    #    placeholder and the files this function patches below are "ours").
    owned = {"build.bat", "release.bat", "CMakeLists.txt", "README.md",
             str(Path("src") / "common" / "game_info.cpp")}
    for item in template_dir.rglob("*"):
        rel = item.relative_to(template_dir)
        dest = project_dir / rel
        if item.is_dir():
            dest.mkdir(parents=True, exist_ok=True)
        elif not dest.exists():
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(item, dest)

    (project_dir / "assets").mkdir(exist_ok=True)

    # 2. fill in the placeholders - the same values the README's manual setup asks for
    build_bat = project_dir / "build.bat"
    build_bat.write_text(_patch_build_bat(build_bat.read_text(), title, "default.xbe", exe_name), newline="")

    release_bat = project_dir / "release.bat"
    release_bat.write_text(_patch_release_bat(release_bat.read_text(), exe_name), newline="")

    cmakelists = project_dir / "CMakeLists.txt"
    cmakelists.write_text(_patch_cmakelists(cmakelists.read_text(), exe_name))

    game_info_cpp = project_dir / "src" / "common" / "game_info.cpp"
    game_info_cpp.write_text(_patch_game_info(
        game_info_cpp.read_text(), title, short_title, window_name, log_name, exe_name))

    readme = project_dir / "README.md"
    readme.write_text(_patch_readme(readme.read_text(), title, origin))

    return project_dir
