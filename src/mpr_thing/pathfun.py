import itertools
import shutil
import time
from pathlib import Path
from typing import Callable, Iterable

from mpremote_path import MPRemotePath as MPath

Dirlist = Iterable[tuple[Path, Iterable[Path]]]

max_depth = 20


def slashify(path: Path | str) -> str:
    """Return `path` as a string (with a trailing slash if it is a directory)."""
    s = str(path)
    add_slash = not s.endswith("/") and isinstance(path, Path) and path.is_dir()
    return s + "/" if add_slash else s


def split(
    files: Iterable[Path],
) -> tuple[Iterable[Path], Iterable[Path], Iterable[Path]]:
    """Split files into directories, files and missing."""
    dirs, files, missing = itertools.tee(files, 3)
    return (
        (f for f in dirs if f.is_dir()),
        (f for f in files if f.is_file()),
        (f for f in missing if not f.exists()),
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


def default_formatter(path: Path) -> str:
    return path.name


def print_files(
    files: Iterable[Path],
    opts: str,
    formatter: Callable[[Path], str] = default_formatter,
) -> None:
    """Print a file listing (long or short style) from data returned
    from the board."""
    # Pretty printing for files on the board
    files = list(files)
    if not files:
        return
    columns = shutil.get_terminal_size().columns
    if "l" in opts:
        # Long listing style - data is a list of filenames
        for f in files:
            st = f.stat()
            size = st.st_size if not f.is_dir() else 0
            t = time.strftime("%c", time.localtime(st.st_mtime)).replace(" 0", "  ")
            print(f"{size:9d} {t[:-3]} {formatter(f)}")
    else:
        # Short listing style - data is a list of filenames
        if len(files) < 20 and sum(len(f.name) + 2 for f in files) < columns:
            # Print all on one line
            for f in files:
                print(formatter(f), end="  ")
            print("")
        else:
            # Print in columns - by row
            w = max(len(f.name) for f in files) + 2
            spaces = " " * w
            cols = columns // w
            for i, f in enumerate(files, start=1):
                print(
                    formatter(f),
                    spaces[len(f.name) :],
                    sep="",
                    end=("" if i % cols and i < len(files) else "\n"),
                )


def ls_files(files: Iterable[Path], recursive: bool = False) -> Dirlist:
    files = list(files)
    yield (Path(), files)
    for f in (f for f in files if f.is_dir()):
        yield from ls_dir(f, max_depth if recursive else 0)


def skip_file(src: Path, dst: Path) -> bool:
    "If local is not newer than remote, return True."
    s, d = src.stat(), dst.stat()
    return (src.is_dir() and dst.is_dir()) or (
        src.is_file()
        and dst.is_file()
        and (d := dst.stat()).st_mtime >= round((s := src.stat()).st_mtime)
        and d.st_size == s.st_size
    )


def check_files(
    cmd: str, filenames: Iterable[Path], dest: Path | None = None, opts: str = ""
) -> tuple[list[Path], Path | None]:
    filelist = list(filenames)
    missing = [str(f) for f in filelist if not f.exists()]
    dirs = [str(d) + "/" for d in filelist if d.is_dir()]
    # Check for invalid requests
    if missing:
        print(f"%{cmd}: Error: Missing files: {missing}.")
        return ([], None)
    if dest:
        for f in filelist:
            if f.is_dir() and f in dest.parents:
                print(f"%{cmd}: Error: {dest!r} is subfolder of {f!r}")
                return ([], None)
            if str(f) == dest:
                print(f"%{cmd}: Error: source is same as dest: {f!r}")
                return ([], None)
    if dirs and cmd in ["rm", "cp", "get", "put"] and "r" not in opts:
        print(f'%{cmd}: Error: Can not process dirs (use "{cmd} -r"): {dirs}')
        return ([], None)

    return (filelist, dest)


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
    slash = "/" if src.is_dir() else ""
    print(f"{src}{slash} -> {dst}{slash}")
    if src.is_dir():
        if not dst.is_dir():
            dst.mkdir()  # "Copy" by creating the destination directory
        return dst
    return copyfile(src, dst)


def rcopy(src: Path, dst: Path) -> None:
    """Copy a file or directory recursively."""
    if copypath(src, dst):
        if src.is_dir():
            for child in src.iterdir():
                rcopy(child, dst / child.name)


def copy_into_dir(src: Path, dst: Path) -> Path | None:
    "Copy `src` into the directory `dst`, which must exist."
    if dst.is_dir():
        return copypath(src, dst / src.name)


def cp_files(files: Iterable[Path], dest: Path) -> None:
    """Copy files and directories on the micropython board.
    If `dest` is an existing directory, move all files into it.
    If `dest` is not an existing directory and there is only one source `file`
    it will be renamed to `dest`.
    Otherwise a `ValueError` is raised.
    """
    it = iter(files)
    if dest.is_dir():
        for f in it:
            rcopy(f, dest / f.name)
    elif (f := next(it, None)) and next(it, None) is None:
        # If there is only one src `path`, make a copy called `dest`
        rcopy(f, dest)
    else:
        raise ValueError(f"%cp: Destination must be a directory: {dest!r}")


def mv_files(paths: Iterable[Path], dest: Path) -> None:
    """Implement the `mv` command to move/rename files and directories.
    If `dest` is an existing directory, move all files/dirs into it.
    If `dest` is not an existing directory and there is only one source `path`
    it will be renamed to `dest`.
    Otherwise a `ValueError` is raised.
    """
    it = iter(paths)
    if dest.is_dir():  # Move all files into the dest directory
        for src in it:
            dst = dest / src.name
            slash = "/" if src.is_dir() else ""
            print(f"{src}{slash} -> {dst}{slash}")
            src.rename(dst)
    elif (src := next(it, None)) is not None and next(it, None) is None:
        # If there is only one src `path`, rename it to `dest`
        slash = "/" if src.is_dir() else ""
        print(f"{src}{slash} -> {dest}{slash}")
        src.rename(dest)
    else:
        raise ValueError(f"%mv: Destination is not a directory: {dest!r}")
