"""Turns whatever the user hands the workflow - a Hugging Face link, a direct
URL to an .iso/.xbe, or a local path to either - into a default.xbe (plus the
disc image, if there was one) ready for xbe_symbols. No manual steps, no
hooks: this is pass 1 of "find everything itself".

Supported `source` forms:
  - a local path to a .xbe, .iso/.xiso, or a .zip containing either
  - a direct http(s) URL to any of the above
  - a Hugging Face URL:
      https://huggingface.co/<user>/<repo>
      https://huggingface.co/datasets/<user>/<repo>
      https://huggingface.co/<user>/<repo>/blob/<rev>/path/to/file.iso
      https://huggingface.co/<user>/<repo>/resolve/<rev>/path/to/file.iso
  - a bare Hugging Face repo id: "<user>/<repo>" or "datasets/<user>/<repo>",
    optionally "@revision", optionally "::path/to/file" to pick one file out
    of a repo that has several
"""
from __future__ import annotations

import dataclasses
import json
import re
import shutil
import urllib.request
import urllib.error
import zipfile
from pathlib import Path
from typing import List, Optional

from xbe_info import XbeInfo, parse_xbe
from xdvdfs import XisoError, extract_default_xbe

USER_AGENT = "XboxXBERecomp-auto-recomp/1.0"
DISC_EXTS = (".iso", ".xiso")
XBE_EXTS = (".xbe",)
ARCHIVE_EXTS = (".zip",)
CANDIDATE_EXTS = DISC_EXTS + XBE_EXTS + ARCHIVE_EXTS


class IngestError(Exception):
    pass


@dataclasses.dataclass
class Ingested:
    xbe_path: Path          # always set: what xbe_symbols / xbe_recomp will read
    disc_path: Optional[Path]  # set when the source was a whole disc image
    source_description: str


def _http_get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.read()
    except urllib.error.HTTPError as e:
        raise IngestError(f"HTTP {e.code} fetching {url}") from e
    except urllib.error.URLError as e:
        raise IngestError(f"could not reach {url}: {e.reason}") from e


def _http_download(url: str, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp, open(dest, "wb") as out:
            shutil.copyfileobj(resp, out, length=1 << 20)
    except urllib.error.HTTPError as e:
        raise IngestError(f"HTTP {e.code} downloading {url}") from e
    except urllib.error.URLError as e:
        raise IngestError(f"could not download {url}: {e.reason}") from e
    return dest


# --- Hugging Face -----------------------------------------------------------

_HF_HOST = "huggingface.co"
_HF_URL_RE = re.compile(
    r"^https?://huggingface\.co/(?P<datasets>datasets/)?(?P<repo>[^/]+/[^/]+?)"
    r"(?:/(?:blob|resolve)/(?P<rev>[^/]+)/(?P<rpath>.+))?/?$"
)
_HF_BARE_RE = re.compile(
    r"^(?P<datasets>datasets/)?(?P<repo>[\w.\-]+/[\w.\-]+?)(?:@(?P<rev>[^:]+))?(?:::(?P<rpath>.+))?$"
)


def _is_huggingface(source: str) -> bool:
    if _HF_HOST in source:
        return True
    # bare "user/repo" shorthand: exactly the owner/name shape, no scheme, no local path chars
    if source.startswith("hf:"):
        return True
    return bool(_HF_BARE_RE.match(source)) and "/" in source and "\\" not in source and not Path(source).exists()


def _hf_list_files(repo: str, is_dataset: bool, revision: str) -> List[str]:
    kind = "datasets" if is_dataset else "models"
    api_url = f"https://huggingface.co/api/{kind}/{repo}/revision/{revision}"
    try:
        info = json.loads(_http_get(api_url))
    except IngestError:
        # some repos 404 on /models/, retry as a dataset (and vice versa)
        kind = "datasets" if not is_dataset else "models"
        api_url = f"https://huggingface.co/api/{kind}/{repo}/revision/{revision}"
        info = json.loads(_http_get(api_url))
        is_dataset = kind == "datasets"
    files = [s["rfilename"] for s in info.get("siblings", [])]
    return files, is_dataset


def _hf_resolve_url(repo: str, is_dataset: bool, revision: str, rpath: str) -> str:
    prefix = "datasets/" if is_dataset else ""
    return f"https://huggingface.co/{prefix}{repo}/resolve/{revision}/{rpath}"


def _pick_best_file(files: List[str]) -> str:
    """Prefers a whole disc image (most faithful: real data layout, no asset
    folder to reconstruct by hand), then a bare .xbe, then an archive."""
    def rank(f: str) -> int:
        low = f.lower()
        if low.endswith(DISC_EXTS):
            return 0
        if low == "default.xbe":
            return 1
        if low.endswith(XBE_EXTS):
            return 2
        if low.endswith(ARCHIVE_EXTS):
            return 3
        return 9

    candidates = [f for f in files if rank(f) < 9]
    if not candidates:
        raise IngestError(
            "no .iso/.xbe/.zip file found in that Hugging Face repo; pass the exact file with "
            "'<repo>::path/to/file.iso'"
        )
    candidates.sort(key=rank)
    return candidates[0]


def _fetch_from_huggingface(source: str, workdir: Path) -> Path:
    is_dataset = False
    rev = "main"
    rpath = None
    repo = None

    if source.startswith("hf:"):
        source = source[3:].lstrip("/")

    m = _HF_URL_RE.match(source)
    if m:
        is_dataset = bool(m.group("datasets"))
        repo = m.group("repo")
        rev = m.group("rev") or "main"
        rpath = m.group("rpath")
    else:
        m = _HF_BARE_RE.match(source)
        if not m:
            raise IngestError(f"could not parse Hugging Face reference: {source}")
        is_dataset = bool(m.group("datasets"))
        repo = m.group("repo")
        rev = m.group("rev") or "main"
        rpath = m.group("rpath")

    if rpath is None:
        files, is_dataset = _hf_list_files(repo, is_dataset, rev)
        rpath = _pick_best_file(files)

    url = _hf_resolve_url(repo, is_dataset, rev, rpath)
    dest = workdir / Path(rpath).name
    print(f"[fetch] Hugging Face: {repo} @ {rev} :: {rpath}")
    _http_download(url, dest)
    return dest


# --- archives ----------------------------------------------------------------

def _extract_archive_for_candidate(archive: Path, workdir: Path) -> Path:
    extract_dir = workdir / (archive.stem + "_extracted")
    extract_dir.mkdir(parents=True, exist_ok=True)
    if archive.suffix.lower() == ".zip":
        with zipfile.ZipFile(archive) as zf:
            zf.extractall(extract_dir)
    else:
        raise IngestError(f"don't know how to extract {archive.name}; only .zip archives are supported")
    best = None
    for p in sorted(extract_dir.rglob("*")):
        if p.is_file() and p.suffix.lower() in DISC_EXTS:
            best = p
            break
    if best is None:
        for p in sorted(extract_dir.rglob("*.xbe")):
            best = p
            break
    if best is None:
        raise IngestError(f"{archive.name}: no .iso or .xbe found inside the archive")
    return best


# --- top level ----------------------------------------------------------------

def ingest(source: str, workdir: Path) -> Ingested:
    """Resolves `source` down to a default.xbe (and the disc image it came
    from, if any), downloading / extracting as needed. Nothing here asks the
    caller to configure paths by hand."""
    workdir.mkdir(parents=True, exist_ok=True)
    local = Path(source)
    description = source

    if local.exists():
        path = local
    elif source.lower().startswith(("http://", "https://")) and not _is_huggingface(source):
        print(f"[fetch] downloading {source}")
        path = _http_download(source, workdir / Path(source.split("?")[0]).name)
    elif _is_huggingface(source):
        path = _fetch_from_huggingface(source, workdir)
    else:
        raise IngestError(
            f"'{source}' is not a local file, a URL, or a Hugging Face reference I recognise"
        )

    if path.suffix.lower() in ARCHIVE_EXTS:
        print(f"[fetch] extracting archive {path.name}")
        path = _extract_archive_for_candidate(path, workdir)

    if path.suffix.lower() in DISC_EXTS:
        print(f"[fetch] reading XDVDFS volume: {path.name}")
        xbe_path = workdir / "default.xbe"
        try:
            extract_default_xbe(path, xbe_path)
        except XisoError as e:
            raise IngestError(str(e)) from e
        return Ingested(xbe_path=xbe_path, disc_path=path, source_description=description)

    if path.suffix.lower() in XBE_EXTS:
        xbe_path = workdir / "default.xbe"
        if path.resolve() != xbe_path.resolve():
            shutil.copyfile(path, xbe_path)
        return Ingested(xbe_path=xbe_path, disc_path=None, source_description=description)

    raise IngestError(f"'{path.name}': not a .xbe, .iso/.xiso or .zip I can use")


def read_xbe_info(ingested: Ingested) -> XbeInfo:
    return parse_xbe(ingested.xbe_path)
