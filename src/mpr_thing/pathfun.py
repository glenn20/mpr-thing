import itertools
import shutil
from pathlib import Path
from typing import Iterable

from mpremote_path import MPRemotePath as MPath

Dirlist = Iterable[tuple[Path, Iterable[Path]]]

max_depth = 20


def slashify(path: Path | str) -> str:
    s = str(path)
    add_slash = not s.endswith("/") and not isinstance(path, str) and path.is_dir()
    return s + "/" if add_slash else s


def split(files: Iterable[Path]) -> tuple[list[Path], list[Path], list[Path]]:
    """Split files into directories, files and missing."""
    dirs, files, missing = itertools.tee(files, 3)
    return (
        [f for f in dirs if f.is_dir()],
        [f for f in files if f.is_file()],
        [f for f in missing if not f.exists()],
    )


def ls_dir(path: Path, depth: int = max_depth) -> Dirlist:
    """Return a directory list of `path` (must be directory) up to `depth` deep.
    If `depth` is 0, only the top level directory is listed."""
    if path.is_dir():
        files = [f for f in path.iterdir()]
        yield (path, files)
        if depth > 0:
            for child in (f for f in files if f.is_dir()):
                yield from ls_dir(child, depth - 1)


def ls_files(files: Iterable[Path], recursive: bool = False) -> Dirlist:
    files = list(files)
    yield (Path(), files)
    for f in (f for f in files if f.is_dir()):
        yield from ls_dir(f, max_depth if recursive else 0)


def copypath(src: Path, dst: Path) -> None:
    """Copy a file, with optimisations for mpremote paths."""
    if src.is_dir():
        dst.mkdir()
    elif not src.is_file():
        pass  # skip non regular files
    elif isinstance(src, MPath) and isinstance(dst, MPath):
        src.copy(dst)
    elif isinstance(src, MPath) and not isinstance(dst, MPath):
        with src.board.raw_repl() as r:
            r.fs_get(str(src), str(dst))
    elif not isinstance(src, MPath) and isinstance(dst, MPath):
        with dst.board.raw_repl() as r:
            r.fs_put(str(src), str(dst))
    elif not isinstance(src, MPath) and not isinstance(dst, MPath):
        shutil.copyfile(src, dst)
    else:
        dst.write_bytes(src.read_bytes())


def rcopy(src: Path, dst: Path) -> None:
    """Copy a file or directory recursively."""
    slash = "/" if src.is_dir() else ""
    print(f"{src}{slash} -> {dst}{slash}/")
    copypath(src, dst)
    if src.is_dir():
        for child in src.iterdir():
            rcopy(child, dst / child.name)


def cp_files(files: Iterable[Path], dest: Path) -> None:
    "Copy files and directories on the micropython board."
    if dest.is_dir():
        for f in files:
            rcopy(f, dest / f.name)
    elif (f := next(it := iter(files), None)) and next(it, None) is None:
        # `cp f1 f2` or `cp d1 d2` where d2 does not exist
        rcopy(f, dest)
    else:
        raise ValueError(f"%cp: Destination must be a directory: {dest!r}")


def mv_files(files: Iterable[Path], dest: Path) -> None:
    "Copy files and directories on the micropython board."
    if dest.is_dir():
        for f in files:
            f.rename(dest / f.name)
    elif (f := next(it := iter(files), None)) and next(it, None) is None:
        # `cp f1 f2` or `cp d1 d2` where d2 does not exist
        f.rename(dest)
    else:
        raise ValueError(f"%mv: Destination must be a directory: {dest!r}")
