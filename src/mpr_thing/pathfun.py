import itertools
import shutil
from pathlib import Path
from typing import Any, Iterable

from mpremote_path import MPRemotePath as MPath

Dirlist = Iterable[tuple[Path, Iterable[Path]]]

max_depth = 20


def mpath(f: Any) -> MPath:
    return f if isinstance(f, MPath) else MPath(str(f))


def slashify(path: Path | str) -> str:
    s = str(path)
    add_slash = not s.endswith("/") and isinstance(path, Path) and path.is_dir()
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


def skip_file(src: Path, dst: MPath) -> bool:
    "If local is not newer than remote, return True."
    s, d = src.stat(), dst.stat()
    return (src.is_dir() and dst.is_dir()) or (
        src.is_file()
        and dst.is_file()
        and (d := dst.stat()).st_mtime >= round((s := src.stat()).st_mtime)
        and d.st_size == s.st_size
    )


def check_files(
    cmd: str, filenames: Iterable[str | MPath], dest: str = "", opts: str = ""
) -> tuple[list[MPath], MPath | None]:
    filelist = [mpath(f) for f in ([*filenames, dest] if dest else filenames)]
    dest_f = filelist.pop() if dest else None
    missing = [str(f) for f in filelist if not f.exists()]
    dirs = [str(d) + "/" for d in filelist if d.is_dir()]
    # Check for invalid requests
    if missing:
        print(f"%{cmd}: Error: Missing files: {missing}.")
        return ([], None)
    if dest_f:
        for f in filelist:
            if f.is_dir() and f in dest_f.parents:
                print(f"%{cmd}: Error: {dest!r} is subfolder of {f!r}")
                return ([], None)
            if str(f) == dest:
                print(f"%{cmd}: Error: source is same as dest: {f!r}")
                return ([], None)
    if dirs and cmd in ["rm", "cp", "get", "put"] and "r" not in opts:
        print(f'%{cmd}: Error: Can not process dirs (use "{cmd} -r"): {dirs}')
        return ([], None)

    return (filelist, dest_f)


def copyfile(src: Path, dst: Path) -> Path | None:
    """Copy a file, with optimisations for mpremote paths."""
    if not src.is_file():
        return None  # skip non regular files
    elif isinstance(src, MPath) and isinstance(dst, MPath):
        src.copy(dst)  # Both files are on the micropython board
    elif isinstance(src, MPath) and not isinstance(dst, MPath):
        with src.board.raw_repl() as r:
            r.fs_get(str(src), str(dst))  # Copy from micropython board to local
    elif not isinstance(src, MPath) and isinstance(dst, MPath):
        with dst.board.raw_repl() as r:
            r.fs_put(str(src), str(dst))  # Copy from local to micropython board
    elif not isinstance(src, MPath) and not isinstance(dst, MPath):
        shutil.copyfile(src, dst)  # Copy local file to local file
    else:
        dst.write_bytes(src.read_bytes())  # Fall back to copying file content
    return dst


def copypath(src: Path, dst: Path) -> Path | None:
    """Copy a file or directory.
    If `src` is a regular file, call `copyfile()` to copy it to `dst`.
    If `src` is a directory, and `dst` is not a directory, make the new
    directory.
    Returns `dst` if successful, otherwise returns `None`."""
    if src.is_dir():
        if not dst.is_dir():
            dst.mkdir()  # "Copy" by creating the destination directory
        return dst
    return copyfile(src, dst)


def rcopy(src: Path, dst: Path) -> None:
    """Copy a file or directory recursively."""
    if copypath(src, dst):
        slash = "/" if src.is_dir() else ""
        print(f"{src}{slash} -> {dst}{slash}")
        if src.is_dir():
            for child in src.iterdir():
                rcopy(child, dst / child.name)


def copy_into_dir(src: Path, dst: Path) -> Path | None:
    "Copy files and directories on the micropython board."
    if dst.is_dir():
        return copypath(src, dst / src.name)


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
