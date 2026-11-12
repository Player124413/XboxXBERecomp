# auto_recomp - the universal, hook-free recompilation workflow

One command (or one GitHub Actions run) takes a game from a link to two
separate deliverables: a compiled exe, and the recompiled C++ source. It does
everything the manual ["Game Setup"](../../README.md#game-setup) steps ask
for by itself - no `[[hook]]`, no `[[function]]`, no `X86_FUNC` is ever
written by this tool. What it can't resolve automatically (an XDK build the
SDK has no signatures for yet) it reports clearly and stops, instead of
guessing.

## Usage

```
python sdk/tools/auto_recomp/recomp.py --source <source>
```

`<source>` is any of:

- a Hugging Face link: `https://huggingface.co/user/repo`,
  `https://huggingface.co/datasets/user/repo`, a `/blob/` or `/resolve/` link
  to one file, or the short forms `user/repo`, `user/repo@revision`,
  `user/repo::path/to/file.iso`
- a direct URL to a `.iso`/`.xiso`, `default.xbe`, or a `.zip` with one inside
- a local path to any of the above

The game's title, executable name and XDK symbols are all found
automatically:

1. **fetch** (`fetch.py`) - downloads the source if it's remote, reads an
   `.iso`'s XDVDFS filesystem directly (no `extract-xiso` or other external
   tool needed) to pull out `default.xbe`, and unzips an archive if needed.
2. **identify** (`xbe_info.py`) - reads the XBE header and certificate for
   the game's title, title ID and XDK library builds.
3. **scaffold** (`scaffold.py`) - creates `sdk/games/<slug>` from
   [`templates/game`](../../templates/game), filling in the title, window
   name, settings/log file names and the `[xbe]`/image table entries that the
   README's manual setup asks for. `app::kGame` stays exactly as hook-free as
   the template ships it: every callback is left `nullptr`.
4. **symbols** - runs `xbe_symbols` to match the XBE against
   [`sdk/xdk/*/signatures.toml`](../../xdk) and write `config/game.toml`,
   same as the first `build.bat` run does. A library build the SDK has no
   signatures for yet is the one thing this can't invent; it stops and says
   so rather than producing a broken config.
5. **build** - `cmake --preset release` + `cmake --build`, Windows only (the
   SDK's renderer and kernel are Win32/Direct3D 12). Steps 1-4 need no
   compiler and run on any OS; on Linux/macOS the command stops after writing
   `config/game.toml`, with the scaffolded project ready for `build.bat` on
   Windows.
6. **package** - copies the built executable (plus its code `.dll` and the
   Visual C++ runtime, found the same way `release.bat` does) into `dist/<slug>/exe/`,
   and the scaffolded project together with the recompiler's generated C++
   into `dist/<slug>/source/` - zipped as `dist/<slug>/<Exe>.zip` and
   `dist/<slug>/<Exe>-source.zip`.

Re-running the same command is safe: ingestion, scaffolding and symbol
resolution are all idempotent and only touch the files they own (never
`assets/`, never a `config/game.toml` that already has `[symbols]`, never
anything you add under `src/game`).

Useful flags: `--title`, `--exe`, `--slug`, `--games-root`, `--out`,
`--skip-build` (stop once `config/game.toml` is written, don't compile).

## GitHub Actions

[`auto_recomp.yml`](../../.github/workflows/auto_recomp.yml) runs the same
script on a `windows-latest` runner from a manual trigger (Actions ->
Automated recompilation -> Run workflow), taking the same `source` (plus
optional `title`/`exe_name`) as inputs, and publishes `recompiled-exe` and
`recompiled-source` as separate workflow artifacts.

## What this does *not* do

- It does not include or download any game's copyrighted assets as part of
  the deliverables; only the XBE's own code is read, to generate code from it.
  `exe/assets` ships empty, exactly like `release.bat`'s output.
- It cannot generate XDK signatures for a library build the SDK has never
  seen; see the main README's "A new XDK build needs..." paragraph for how
  those come from a finished, already-configured title.
- It never touches `src/game` or writes a `[[hook]]`/`[[function]]`/`[[switch]]`
  override - if a specific title needs one of those to run correctly, that
  stays a manual step for a human who wants to improve that port, same as
  every other port this SDK has shipped.
