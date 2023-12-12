"""Provides the "Board" class which is a wrapper around the
PyBoardExtended interface to a micropython board (from the mpremote tool).
"""
# Copyright (c) 2021 @glenn20
# MIT License

# For python<3.10: Allow method type annotations to reference enclosing class
from __future__ import annotations

import os
import re
from enum import IntFlag
from pathlib import Path
from typing import Any, Callable, Iterable, Optional, Sequence

from mpremote.transport_serial import SerialTransport
from mpremote_path import Board
from mpremote_path import MPRemotePath as MPath

from . import pathfun

# Type aliases
Writer = Callable[[bytes], None]  # Type of the console write functions
PathLike = str | os.PathLike  # Accepts str or Pathlib for filenames
RemoteFilename = str | MPath
RemoteFilenames = Iterable[RemoteFilename]
RemoteDirlist = Iterable[tuple[str, Iterable[MPath]]]
Dirlist = Iterable[tuple[Path, Iterable[Path]]]

CODE_COMPRESS_RULES: list[tuple[bytes, bytes, dict[str, int]]] = [
    (b" *#.*$", b"", {"flags": re.MULTILINE}),  # Delete comments
    (b"    ", b" ", {}),  # Replace 4 spaces with 1
    (rb"([,;])  *", rb"\1", {}),  # Remove spaces after , and ;
    (rb"  *([=+-])  *", rb"\1", {}),  # Remove spaces around =, + and -
]


class Debug(IntFlag):
    NONE = 0
    EXEC = 1
    FILES = 2


def mpath(f: Any) -> MPath:
    return f if isinstance(f, MPath) else MPath(str(f))


# A collection of helper functions for file listings and filename completion
# to be uploaded to the micropython board and processed on the local host.
class MPBoard:
    """A wrapper for the transport classes from the mpremote tool.
    Provides convenience methods and wrappers for filesystem operations
    on a micropython board.
    """

    def __init__(self, transport: SerialTransport, writer: Writer) -> None:
        """Construct a "Board" instance.

        Args:
            pyb: An instance of the PyboardExtended class from mpremote tool.
            writer: A function to print output from the micropython board.
        """
        self.board: Board = Board(transport)
        self.writer = writer
        self.debug: Debug = Debug.NONE
        self.helper_loaded = False
        MPath.connect(transport)

    def reset(self) -> None:
        self.helper_loaded = False

    def load_helper(self) -> None:
        "Load the __helper class and methods onto the micropython board."
        if self.helper_loaded:
            return
        self.helper_loaded = True

    def device_name(self) -> str:
        "Get the name of the serial port connected to the micropython board."
        return str(self.board._transport.device_name)

    def write(self, response: bytes | str) -> None:
        'Call the console writer for output (convert "str" to "bytes").'
        if response:
            if isinstance(response, str):
                response = bytes(response, "utf-8")
            self.writer(response)

    def raw_repl(self, message: Any = "") -> Any:
        "Return a context manager for the micropython raw repl."
        return self.board.raw_repl(message)

    # Execute stuff on the micropython board
    def exec(self, code: bytes | str, silent: bool = True) -> str:
        "Execute some code on the micropython board."
        return self.board.exec(code)

    # Execute stuff on the micropython board
    def eval(self, expression: bytes | str) -> Any:
        "Evaluate some code on the micropython board."
        return self.board.eval(expression)

    def complete(self, word: str) -> list[str]:
        "Complete the python name on the board."
        words = [""] + word.rsplit(".", 1)  # Split module and class names
        root, base = repr(words[-2]) if words[-2] else "", repr(words[-1])
        return self.board.eval(f"[w for w in dir({root}) if w.startswith({base})]")

    def path(self, filename: RemoteFilename) -> MPath:
        "Return the full path of a file on the board."
        return mpath(filename)

    def cat(self, filename: str) -> None:
        'List the contents of the file "filename" on the board.'
        print(mpath(filename).read_text(), end="")

    def touch(self, filename: RemoteFilename) -> None:
        mpath(filename).touch()

    def cd(self, filename: str) -> None:
        mpath(filename).chdir()

    def pwd(self) -> str:
        return str(MPath.cwd())

    def mkdir(self, filename: str) -> None:
        mpath(filename).mkdir()

    def rmdir(self, filename: str) -> None:
        mpath(filename).rmdir()

    def ls_files(self, filenames: RemoteFilenames) -> Iterable[MPath]:
        "Return a list of files (MPRemotePath) on board for list of filenames."
        # Board returns: {"f1": [s0, s1, s2], "f2": [s0, s1, s2], ...}
        # Where s0 is mode, s1 is size and s2 is mtime
        return map(mpath, filenames)

    def ls(self, files: RemoteFilenames, opts: str = "") -> Dirlist:
        """Return a listing of files in directories on the board.
        Takes a list of directory pathnames and a listing options string.
        Returns an iterable over: [(dirname, [Path1, Path2, Path3..]), ...]
        """
        # From the board: {
        #  "dir":  {"f1": [mode, size, mtime], "f2": [mode..], ...},
        #  "dir2": {"f1": [mode, size, mtime], "f2": [mode..], ...}, ...
        # }
        return pathfun.ls_files(map(mpath, files), "R" in opts or "r" in opts)

    def ls_dir(self, directory: RemoteFilename) -> Iterable[str]:
        """Return the list of files in a directory on the board."""
        return (str(f) for f in mpath(directory).iterdir())

    def check_files(
        self, cmd: str, filenames: RemoteFilenames, dest: str = "", opts: str = ""
    ) -> tuple[list[MPath], Optional[MPath]]:
        filelist = list(self.ls_files([*filenames, dest] if dest else filenames))
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

    def rm(self, filenames: RemoteFilenames, opts: str) -> None:
        for f in map(mpath, filenames):
            if "v" in opts:
                print(f.as_posix())
            if f.is_file():
                f.unlink()
            elif f.is_dir():
                if "r" in opts:
                    self.rm(f.iterdir(), opts)
                f.rmdir()

    def mv(self, filenames: RemoteFilenames, dest: str, opts: str) -> None:
        filelist, dest_f = self.check_files("mv", filenames, dest, opts)
        if dest_f:
            pathfun.mv_files(filelist, dest_f)

    def cp(self, filenames: RemoteFilenames, dest: str, opts: str) -> None:
        "Copy files and directories on the micropython board."
        filelist, dest_f = self.check_files("cp", filenames, dest, opts)
        if dest_f:
            pathfun.cp_files(filelist, dest_f)

    def get(self, filenames: RemoteFilenames, dest: PathLike, opts: str = "") -> None:
        "Copy files and directories from the board to a local folder:"
        pathfun.cp_files((mpath(f) for f in filenames), Path(dest))

    def skip_file(self, source: Path, dest: MPath) -> bool:
        "If local is not newer than remote, return True."
        s = source.stat()
        size, mtime = s[6], round(s[8])
        return (source.is_dir() and dest.is_dir()) or (
            source.is_file()
            and dest.is_file()
            and dest.stat().st_mtime >= mtime
            and dest.stat().st_size == size
        )

    def put(
        self, filenames: Iterable[str], destname: RemoteFilename, opts: str = ""
    ) -> None:
        "Copy local files to the current folder on the board."
        pathfun.cp_files((Path(f) for f in filenames), mpath(destname))

    def df(self, dirs: RemoteFilenames) -> Sequence[tuple[str, int, int, int]]:
        ret: list[tuple[str, int, int, int]] = []
        for d in dirs or ["/"]:
            _, bsz, tot, free, *_ = self.board.eval(f"os.statvfs({str(d)!r})")
            ret.append((str(d), tot * bsz, (tot - free) * bsz, free * bsz))
        return ret

    def gc(self) -> tuple[int, int]:
        before, after = self.board.exec_eval(
            "import gc;_b=gc.mem_free();gc.collect();print([_b,gc.mem_free()])"
        )
        return (int(before), int(after))
