import shutil
import time
from pathlib import Path
from typing import Callable, Iterable

from mpremote_path import MPRemotePath as MPath

Dirfiles = tuple[Path | None, Iterable[Path]]
Dirlist = Iterable[Dirfiles]

max_depth = 20


def slashify(path: Path | str) -> str:
    """Return `path` as a string (with a trailing slash if it is a directory)."""
    s = str(path)
    add_slash = not s.endswith("/") and isinstance(path, Path) and path.is_dir()
    return s + "/" if add_slash else s


def split_files(
    files: Iterable[Path],
) -> tuple[list[Path], list[Path], list[Path]]:
    """Split files into directories, files and missing."""
    all = list(files)
    return (
        [f for f in all if f.is_dir()],  # Directories
        [f for f in all if f.is_file()],  # Files
        [f for f in all if not f.exists()],  # Missing files
    )


def dirlist_dir(path: Path, depth: int = max_depth) -> Dirlist:
    """Return a directory list of `path` (must be directory) up to `depth` deep.
    If `depth` is 0, only the top level directory is listed."""
    if path.is_dir():
        files = sorted(path.iterdir())
        yield (path, files)
        if depth > 0:
            for child in (f for f in files if f.is_dir()):
                yield from dirlist_dir(child, depth - 1)


def dirlist(files: Iterable[Path], recursive: bool = False) -> Dirlist:
    files = sorted(files)
    yield (None, files)
    for f in (f for f in files if f.is_dir()):
        yield from dirlist_dir(f, max_depth if recursive else 0)


def default_formatter(path: Path) -> str:
    return path.name


def print_ls_long(
    dirlist: Dirlist,
    formatter: Callable[[Path], str] = default_formatter,
) -> None:
    """Print a long-style file listing from a `Dirlist`."""
    for directory, files in dirlist:
        if directory:
            print(f"{formatter(directory)}:")
        for f in files:
            st = f.stat()
            size = st.st_size if not f.is_dir() else 0
            t = time.strftime("%c", time.localtime(st.st_mtime)).replace(" 0", "  ")
            print(f"{size:9d} {t[:-3]} {formatter(f)}")


def print_ls_short(
    dirlist: Dirlist,
    formatter: Callable[[Path], str] = default_formatter,
) -> None:
    """Print a short-style file listing from a `Dirlist`."""
    started = False
    columns = shutil.get_terminal_size().columns
    for directory, files in dirlist:
        files = list(files)
        if started:
            print()  # Add a blank line between directory listings
        if directory:  # The first entry is None signifying the top level file list
            print(f"{formatter(directory)}:")
            started = True
        if not files:
            pass
        elif len(files) < 20 and sum(len(f.name) + 2 for f in files) < columns:
            # Print all on one line
            print("  ".join(formatter(f) for f in files))
            started = True
        else:
            # Print in columns - by row
            w = max(len(f.name) for f in files) + 2
            spaces = " " * (w - 1)
            cols = columns // w
            for i, f in enumerate(files, start=1):
                print(formatter(f), spaces[len(f.name) :], end="")
                if i % cols == 0 or i == len(files):
                    print()
                started = True


def print_ls(
    dirlist: Dirlist,
    long_style: bool = False,
    formatter: Callable[[Path], str] = default_formatter,
) -> None:
    """Print a file listing (just the names) from `dirlist`.
    - `dirlist` is an iterable like: `[(dir1, [file1,...]),...]`, where `dirX`
      and `fileX` are instances of `Path`.
    - `long_listing` (bool): if `True`, print a long-style file listing.
    - `formatter` is a function that takes a `Path` and returns a string. Eg.
      this is used to colourise the filenames in thne output."""
    if long_style:
        print_ls_long(dirlist, formatter)
    else:
        print_ls_short(dirlist, formatter)


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
