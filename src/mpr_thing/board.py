"""Provides the "Board" class which is a wrapper around the
PyBoardExtended interface to a micropython board (from the mpremote tool).
"""
# Copyright (c) 2021 @glenn20
# MIT License

# For python<3.10: Allow method type annotations to reference enclosing class
from __future__ import annotations

import itertools
import json
import os
import re
import time
from enum import IntFlag
from pathlib import Path
from typing import Any, Callable, Iterable, Optional, Sequence

from mpremote.transport_serial import SerialTransport

from .catcher import raw_repl
from .remote_path import RemotePath

# Type aliases
Writer = Callable[[bytes], None]  # Type of the console write functions
PathLike = str | os.PathLike  # Accepts str or Pathlib for filenames
RemoteFilename = str | RemotePath
RemoteFilenames = Iterable[RemoteFilename]
RemoteDirlist = Iterable[tuple[str, Iterable[RemotePath]]]

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


class RemoteFolder:
    def __init__(self, ls: RemoteDirlist) -> None:
        self.ls = {str(f): f for _, files in ls for f in files}

    def __getitem__(self, file: str | RemotePath) -> RemotePath:
        return self.ls.get(str(file)) or RemotePath(str(file))


def slashify(path: Any) -> str:
    s = str(path)
    return s if s[-1:] == "/" else s + "/"


# A collection of helper functions for file listings and filename completion
# to be uploaded to the micropython board and processed on the local host.
class Board:
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
        self.transport: SerialTransport = transport  # type: ignore
        self.writer = writer
        self.debug: Debug = Debug.NONE
        self.board_has_utime: bool = False  # See PR#9644
        self.helper_loaded = False
        # RemotePath._board = self

    def reset(self) -> None:
        self.helper_loaded = False

    def load_helper(self) -> None:
        "Load the __helper class and methods onto the micropython board."
        if self.helper_loaded:
            return
        # The helper code is "board/cmd_helper.py" in the module directory.
        micropy_file = Path(__file__).parent / "board" / "cmd_helper.py"
        with open(micropy_file, "rb") as f:
            code = f.read()
        for a, b, flags in CODE_COMPRESS_RULES:
            code = re.sub(a, b, code, **flags)
        self.exec(code)
        self.check_time_offset()
        self.board_has_utime = bool(
            self.eval_json("print(int('utime' in os.__dict__))")
        )
        self.helper_loaded = True

    def check_time_offset(self) -> None:
        tt = time.gmtime(time.time())  # Use now as a reference time
        localtm = time.mktime((*tt[:8], -1))  # let python sort out dst
        remotetm: int = self.eval_json(f"import time;print(time.mktime({tt[:8]}))")
        RemotePath.epoch_offset = round(localtm - remotetm)

    def device_name(self) -> str:
        "Get the name of the serial port connected to the micropython board."
        return str(self.transport.device_name)

    def write(self, response: bytes | str) -> None:
        'Call the console writer for output (convert "str" to "bytes").'
        if response:
            if isinstance(response, str):
                response = bytes(response, "utf-8")
            self.writer(response)

    def raw_repl(self, message: Any = None) -> Any:
        "Return a context manager for the micropython raw repl."
        return raw_repl(self.transport, self.write, message)

    # Execute stuff on the micropython board
    def exec(self, code: bytes | str, silent: bool = True) -> str:
        "Execute some code on the micropython board."
        response: str = ""
        if self.debug & Debug.EXEC:
            print(f"Board.exec(): code = {code!r}")
        with self.raw_repl(code):
            writer = self.writer if not silent else None
            response = self.transport.exec(code, writer).decode().strip()
        if self.debug & Debug.EXEC:
            print(f"Board.exec(): resp = {response}")
        return response

    def eval_json(self, code: str) -> Any:
        """Execute code on board and interpret the output as json.
        Single quotes (') in output will be changed to " before processing."""
        response = self.exec(code)
        # Safer to use json to construct objects rather than eval().
        # Exceptions will be caught at the top level.
        return json.loads(response.replace("'", '"'))

    def complete(self, word: str) -> list[str]:
        "Complete the python name on the board."
        words = [""] + word.rsplit(".", 1)  # Split module and class names
        completions: list[str] = self.eval_json(
            f"_helper.complete({words[-2]!r}, {words[-1]!r})"
        )
        return completions

    def cat(self, filename: str) -> None:
        'List the contents of the file "filename" on the board.'
        with self.raw_repl():
            self.transport.fs_cat(filename)

    def touch(self, filename: str) -> None:
        with self.raw_repl():
            self.transport.fs_touch(filename)

    def cd(self, filename: str) -> None:
        self.exec(f"os.chdir({filename!r})")

    def pwd(self) -> str:
        pwd: str = self.eval_json('print("\\"{}\\"".format(os.getcwd()))')
        return pwd

    def mkdir(self, filename: str) -> None:
        with self.raw_repl():
            self.transport.fs_mkdir(filename)

    def rmdir(self, filename: str) -> None:
        with self.raw_repl():
            self.transport.fs_rmdir(filename)

    def mount(self, directory: str, opts: str = "") -> None:
        path = os.path.realpath(directory)
        if not os.path.isdir(path):
            print("%mount: No such directory:", path)
            return
        with self.raw_repl():
            self.transport.mount_local(path, unsafe_links="l" in opts)

    def umount(self) -> None:
        'Unmount any Virtual Filesystem mounted at "/remote" on the board.'
        # Must chdir before umount or bad things happen.
        self.exec('os.getcwd().startswith("/remote") and os.chdir("/")')
        with self.raw_repl():
            self.transport.umount_local()

    def ls_files(self, filenames: RemoteFilenames) -> Iterable[RemotePath]:
        "Return a list of files (RemotePath) on board for list of filenames."
        # Board returns: [["f1", s0, s1, s2], ["f2", s0, s1, s2], ...]]
        # Where s0 is mode, s1 is size and s2 is mtime
        ls = self.eval_json(f"_helper.ls_files({[str(f) for f in filenames]})")
        return (RemotePath(f[0]).set_modes(f[1:]) for f in ls)

    def ls_dirs(self, dir_list: RemoteFilenames, opts: str = "") -> RemoteDirlist:
        """Return a listing of files in directories on the board.
        Takes a list of directory pathnames and a listing options string.
        Returns an iterable over: [(dirname, [Path1, Path2, Path3..]), ...]
        """
        # From the board: [
        #  ["dir",  [["f1" s0, s1, s2], ["f2", s0..], ..]],
        #  ["dir2", [["f1" s0, s1, s2], ["f2", s0..], ..]], ...
        # ]
        remotefiles: RemoteDirlist = []
        opts = f"{'R' in opts},{'l' in opts}"
        listing = self.eval_json(
            f"_helper.ls_dirs({[slashify(d) for d in dir_list]},{opts})"
        )
        listing.sort(key=lambda d: d[0])  # Sort by directory pathname
        for _, file_list in listing:
            # sort each directory listing by filename
            file_list.sort(key=lambda f: f[0])
        remotefiles = (  # Convert to lists of RemotePath objects
            (dirname, (RemotePath(dirname, f[0]).set_modes(f[1:]) for f in filelist))
            for dirname, filelist in listing
        )
        return remotefiles

    def ls_dir(self, directory: RemoteFilename) -> Iterable[RemotePath]:
        dir_files = next(iter(self.ls_dirs([str(directory)])))
        return dir_files[1] if dir_files else []

    def ls(self, filenames: RemoteFilenames, opts: str) -> RemoteDirlist:
        "Return a list of files on the board."
        filenames = sorted(filenames)
        filelist = list(self.ls_files(filenames))  # We parse this several times
        missing = (f for f in filelist if not f.exists())
        files = (f for f in filelist if f.is_file())
        dirs = (d for d in filelist if d.is_dir())
        for f in missing:
            print(f"ls: cannot access {f.as_posix()!r}: No such file or directory")
        lsdirs = self.ls_dirs(dirs if filenames else ["./"], opts)
        return itertools.chain([("", list(dirs) + list(files))], lsdirs)

    def remotefile(self, filename: RemoteFilename) -> RemotePath:
        "Return a RemotePath object for the filename on the board."
        return next(iter(self.ls_files((filename,))))

    def remotefiles(self, filenames: RemoteFilenames) -> Iterable[RemotePath]:
        "Return a list of RemotePath objects for the filename on the board."
        return self.ls_files(filenames)

    def remotefolder(self, folder: RemoteFilename) -> RemoteFolder:
        "Return a RemotePath object for the filename on the board."
        return RemoteFolder(self.ls((folder,), "-lR"))

    def check_files(
        self, cmd: str, filenames: RemoteFilenames, dest: str = "", opts: str = ""
    ) -> tuple[list[RemotePath], Optional[RemotePath]]:
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
        filelist, _ = self.check_files("rm", filenames, "", opts)
        opts = f"{'v' in opts},{'n' in opts}"
        self.exec(f"_helper.rm({[str(f) for f in filelist]},{opts})", silent=False)

    def mv(self, filenames: RemoteFilenames, dest: str, opts: str) -> None:
        filelist, dest_f = self.check_files("mv", filenames, dest, opts)
        if not filelist or not dest_f:
            return
        if len(filelist) == 1 and not dest_f.is_dir():
            # First - check for special cases...
            f = filelist[0]
            if not dest_f.exists() or f.is_file():
                if "v" in opts:
                    print(f"{str(f)} -> {str(dest)}")
                self.exec(f"os.rename({str(f)!r},{dest!r})")
            else:
                print(f"%mv: Error: Destination must be directory: {dest!r}")
            return
        # Move files to dest which is a directory
        for f in filelist:
            f2 = dest_f / f.name
            if "v" in opts:
                print(f"{str(f)} -> {str(f2)}")
            self.exec(f"os.rename({str(f)!r},{str(f2)!r})")

    def cp(self, filenames: RemoteFilenames, dest: str, opts: str) -> None:
        "Copy files and directories on the micropython board."
        filelist, dest_f = self.check_files("cp", filenames, dest, opts)
        if not filelist or not dest_f:
            return
        dest = str(dest_f)
        files = [str(f) for f in filelist if f.is_file()]
        dirs = [str(d) + "/" for d in filelist if d.is_dir()]
        opts = f"{'v' in opts},{'n' in opts}"
        if len(filelist) == 1:
            # First - check for some special cases...
            if files and (dest_f.is_file() or not dest_f.exists()):
                # cp file1 file2
                self.exec(
                    f"_helper.cp_file({files[0]!r},{dest!r},{opts})", silent=False
                )
                return
            elif dirs and not dest_f.exists():
                # cp dir1 dir2 (where dir2 does not exist)
                self.exec(
                    f'_helper.cp_dir({dirs[0]!r},{dest + "/"!r},{opts})', silent=False
                )
                return
        if not dest_f.is_dir():
            print(f"%cp: Destination must be a directory: {dest}")
            return
        self.exec(f'_helper.cp({files},{dirs},{dest + "/"!r},{opts})', silent=False)

    def get_file(
        self,
        filename: PathLike,
        dest: PathLike,
        verbose: bool = False,
        dry_run: bool = False,
    ) -> None:
        'Copy a file "filename" from the board to the local "dest" folder.'
        if verbose:
            print(str(dest))
        if not dry_run:
            with self.raw_repl(("get_file", filename, dest)):
                self.transport.fs_get(str(filename), str(dest))

    def get_dir(
        self, directory: PathLike, dest: PathLike, verbose: bool, dry_run: bool
    ) -> None:
        "Recursively copy a directory from the micropython board."
        # dir is subdirectory name for recursive
        base: Optional[Path] = None
        for subdir, filelist in self.ls([str(directory)], "-R"):
            srcdir = Path(subdir)
            # First non-empty subdir is base of a recursive listing
            if subdir and base is None:
                base = Path(subdir).parent
            # Destination subdir is dest + relative path from dir to base
            destdir = (dest / Path(subdir).relative_to(base)) if base else Path()
            if not destdir.is_dir():
                if verbose:
                    print(str(destdir))
                if not dry_run:
                    os.mkdir(destdir)
            for f in filelist:
                f1 = srcdir / f.name
                f2 = destdir / f.name
                if f.is_file():
                    self.get_file(str(f1), str(f2), verbose, dry_run)

    def get(self, filenames: RemoteFilenames, dest: PathLike, opts: str = "") -> None:
        "Copy files and directories from the board to a local folder:"
        dest = Path(dest)
        verbose, dry_run, recursive = (i in opts for i in "vnr")
        filenames = list(filenames)
        if len(filenames) == 1 and not dest.is_dir():
            for f in filenames:  # TODO: What if f is a directory???
                self.get_file(f, str(dest), verbose, dry_run)
            return
        if not dest.is_dir():
            print("get: Destination directory does not exist:", dest)
            return
        with self.raw_repl():
            for file in self.ls_files(filenames):
                # dir: str, file: RemotePathList (list of full pathnames)
                if file.is_file():
                    f2 = dest / file.name
                    self.get_file(str(file), str(f2), verbose, dry_run)
                elif file.is_dir():
                    if recursive:  # Is a directory
                        self.get_dir(file, dest, verbose, dry_run)
                    else:
                        print(
                            f'get: skipping "{str(file)}", '
                            f'use "-r" to copy directories.'
                        )
                elif not file.exists():
                    print(f"{str(file)}: No such file.")

    def skip_file(self, source: Path, dest: RemotePath) -> bool:
        "If local is not newer than remote, return True."
        s = source.stat()
        size, mtime = s[6], round(s[8])
        return (source.is_dir() and dest.is_dir()) or (
            source.is_file()
            and dest.is_file()
            and dest.mtime >= mtime
            and dest.size == size
        )

    def put_file(self, source: Path, dest: RemotePath, opts: str = "") -> None:
        'Copy a local file "filename" to the "dest" folder on the board.'
        if not source.exists():
            raise FileNotFoundError(f"Local file does not exist: '{source}'")
        if source.is_dir() and dest.is_file():
            raise OSError(f"Can not copy local dir to remote file {dest.as_posix()}")
        if source.is_file() and dest.is_dir():
            raise OSError(f"Can not copy local file to remote dir {dest.as_posix()}")
        if self.debug & Debug.FILES:
            print(f"local: {source!r}")
            print(f"remote: {dest!r}")
        if "s" in opts and self.skip_file(source, dest):
            return  # Skip if same size and same time or newer on board
        if "v" in opts:
            print(slashify(dest))
        if "n" in opts:
            return
        if source.is_file():
            self.transport.fs_put(str(source), str(dest))
        elif source.is_dir() and not dest.exists():
            self.mkdir(str(dest))
        if "t" in opts and self.board_has_utime:
            # Requires micropython PR#9644
            mtime = round(source.stat()[8])
            self.exec(f"os.utime({str(dest)!r},(0,{mtime - RemotePath.epoch_offset}))")

    def put_dir(self, source: Path, dest: RemotePath, opts: str = "") -> None:
        "Recursively copy a directory to the micropython board."
        source = source.resolve()
        base = source.parent
        # Destination subdir is dest + basename of file
        destdir = dest / source.name
        for dirname, _, files in os.walk(source):
            subdir = Path(dirname)
            # Dest subdir is dest + relative path from dir to base
            destdir = dest / subdir.relative_to(base)
            d = list(self.ls_files([str(destdir)]))[0]
            self.put_file(subdir, d, opts)
            for f in files:
                self.put_file(subdir / f, destdir / f, opts)

    def put(
        self,
        filenames: Iterable[str],
        destname: RemoteFilename,
        opts: str = "",
    ) -> None:
        "Copy local files to the current folder on the board."
        filenames = list(filenames)
        with self.raw_repl():
            dest = self.remotefile(destname)
            # put localfile :newfilename
            if len(filenames) == 1 and not dest.is_dir():
                self.put_file(Path(filenames[0]), dest, opts)
                return
            if not dest.is_dir():
                raise FileNotFoundError(f"Destination '{destname}' does not exist.")
            # put localfile1 localfile2 ... :dir
            # put -r localfile1 localdir ... :dir
            for filename in filenames:
                file = Path(filename)
                if not file.is_dir():
                    self.put_file(file, dest / file.name, opts)
                elif "r" in opts:  # file is a directory
                    self.put_dir(file, dest, opts)
                else:
                    print(f"put: skipping '{str(file)}' use '-r' to copy directories.")

    def rsync(self, source: PathLike, dest: str, opts: str = "") -> None:
        "Sync local folder to a folder on the board."
        opts += "s"  # Force sync mode on
        with self.raw_repl():
            src = Path(source).resolve()
            dst = self.remotefile(RemotePath(dest) / src.name)
            self.put_file(src, dst, opts)
            if src.is_file():
                return
            remotefolder = self.remotefolder(dst)
            print(remotefolder.ls)
            for local in src.rglob("*"):
                remote = remotefolder[dst / local.relative_to(src)]
                self.put_file(local, remote, opts)

    def df(self, dirs: RemoteFilenames) -> Sequence[tuple[str, int, int, int]]:
        ret: list[tuple[str, int, int, int]] = []
        for d in dirs or ["/"]:
            _, bsz, tot, free, *_ = self.eval_json(
                f'print(list(os.statvfs("{str(d)}")))'
            )
            ret.append((str(d), tot * bsz, (tot - free) * bsz, free * bsz))
        return ret

    def gc(self) -> tuple[int, int]:
        before, after = self.eval_json(
            "import gc;_b=gc.mem_free();gc.collect();print([_b,gc.mem_free()])"
        )
        return (int(before), int(after))

    def get_time(self) -> time.struct_time:
        time_cmd = "import time;print(list(time.localtime()))"
        time_list = list(self.eval_json(time_cmd)) + [-1]  # is_dst = unknown
        return time.struct_time(time_list)
