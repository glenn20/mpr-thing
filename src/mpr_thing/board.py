"""Provides the "Board" class which is a wrapper around the
PyBoardExtended interface to a micropython board (from the mpremote tool).
"""
# Copyright (c) 2021 @glenn20
# MIT License

# For python<3.10: Allow method type annotations to reference enclosing class
from __future__ import annotations

import os, re
from pathlib import Path
import json
import itertools
from typing import Any, Sequence, Iterable, Callable, Optional

from mpremote.pyboardextended import PyboardExtended

from .catcher import raw_repl
from .remote_path import RemotePath

# Type aliases
Writer    = Callable[[bytes], None]     # Type of the console write functions
PathLike  = str | os.PathLike           # Accepts str or Pathlib for filenames
Filenames = Iterable[str]               # Accept single filenames as file list

CODE_COMPRESS_RULES: list[tuple[bytes, bytes, dict[str, int]]] = [
    (b" *#.*$",          b"",     {'flags': re.MULTILINE}),
    (b"    ",            b" ",    {}),
    (br"([,;])  *",      br"\1",  {}),
    (br"  *([=+-])  *",  br"\1",  {}),
]

DEBUG_EXEC = 1


# A collection of helper functions for file listings and filename completion
# to be uploaded to the micropython board and processed on the local host.
class Board:
    """A wrapper for the PyboardExtended class from the mpremote tool.
    Provides convenience methods and wrappers for filesystem operations
    on a micropython board.
    """

    def __init__(self, pyb: PyboardExtended, writer: Writer) -> None:
        """Construct a "Board" instance.

        Args:
            pyb: An instance of the PyboardExtended class from mpremote tool.
            writer: A function to print output from the micropython board.
        """
        self.pyb = pyb
        self.writer = writer
        self.default_depth = 40   # Max recursion depth for cp(), rm()
        self.debug: int = 0

    def load_helper(self) -> None:
        'Load the __helper class and methods onto the micropython board.'
        # The helper code is "board/cmd_helper.py" in the module directory.
        micropy_file = Path(__file__).parent / 'board' / 'cmd_helper.py'
        with open(micropy_file, 'rb') as f:
            code = f.read()
        for a, b, flags in CODE_COMPRESS_RULES:
            code = re.sub(a, b, code, **flags)
        self.exec(code)

    def device_name(self) -> str:
        'Get the name of the device connected to the micropython board.'
        return self.pyb.device_name

    def write(self, response: bytes | str) -> None:
        'Call the console writer for output (convert "str" to "bytes").'
        if response:
            if not isinstance(response, bytes):
                response = bytes(response, 'utf-8')
            self.writer(response)

    def raw_repl(self, message: Any = None) -> Any:
        'Return a context manager for the micropython raw repl.'
        return raw_repl(self.pyb, self.write, message)

    # Execute stuff on the micropython board
    def exec(
            self,
            code:       bytes | str,
            silent:     bool = True
            ) -> str:
        'Execute some code on the micropython board.'
        response: str = ""
        if self.debug & DEBUG_EXEC:
            print(f"Board.exec(): code = {code}")
        with self.raw_repl(code):
            response = self.pyb.exec_(code, self.writer if not silent else None).decode().strip()
        if self.debug & DEBUG_EXEC:
            print(f"Board.exec(): resp = {response}")
        return response

    def eval_json(
            self,
            code:       str,
            ) -> Any:
        '''Execute code on board and interpret the output as json.
        Single quotes (') in output will be changed to " before processing.'''
        response = self.exec(code)
        # Safer to use json to construct objects rather than eval().
        # Exceptions will be caught at the top level.
        return json.loads(response.replace("'", '"'))

    def complete(self, word: str) -> list[str]:
        'Complete the python name on the board.'
        words = [None] + word.rsplit('.', 1)  # Split module and class names
        completions: list[str] = \
            self.eval_json(f'_helper.complete({words[-2]}, "{words[-1]}")')
        return completions

    def cat(self, filename: str) -> None:
        'List the contents of the file "filename" on the board.'
        with self.raw_repl():
            self.pyb.fs_cat(filename)

    def touch(self, filename: str) -> None:
        self.exec(f'open("{filename}", "a").close()')

    def cd(self, filename: str) -> None:
        self.exec(f'uos.chdir({filename!r})')

    def pwd(self) -> str:
        pwd: str = self.eval_json('print("\\"{}\\"".format(uos.getcwd()))')
        return pwd

    def mkdir(self, filename: str) -> None:
        with self.raw_repl():
            self.pyb.fs_mkdir(filename)

    def rmdir(self, filename: str) -> None:
        with self.raw_repl():
            self.pyb.fs_rmdir(filename)

    def mount(self, directory: str, opts: str = "") -> None:
        path = os.path.realpath(directory)
        if not os.path.isdir(path):
            print("%mount: No such directory:", path)
            return
        with self.raw_repl():
            self.pyb.mount_local(path, 'l' in opts)

    def umount(self) -> None:
        'Unmount any Virtual Filesystem mounted at "/remote" on the board.'
        # Must chdir before umount or bad things happen.
        self.exec('uos.getcwd().startswith("/remote") and uos.chdir("/")')
        with self.raw_repl():
            self.pyb.umount_local()

    def ls_files(
            self,
            filenames:  Filenames
            ) -> Iterable[RemotePath]:
        'Return a list of files (RemotePath) on board for list of filenames.'
        # Board returns: [["f1", s0, s1, s2], ["f2", s0, s1, s2], ...]]
        # Where s0 is mode, s1 is size and s2 is mtime
        ls = self.eval_json(f'_helper.ls_files({filenames})')
        return (RemotePath(f[0]).set_modes(f[1:]) for f in ls)

    def ls_dirs(
            self,
            dir_list:   Iterable[str],
            opts:       str = ""
            ) -> Iterable[tuple[str, Iterable[RemotePath]]]:
        """Return a listing of files in directories on the board.
        Takes a list of directory pathnames and a listing options string.
        Returns a list: [(dirname, [Path1, Path2, Path3..]), ...]
        """
        # From the board: [
        #  ["dir",  [["f1" s0, s1, s2], ["f2", s0..], ..]],
        #  ["dir2", [["f1" s0, s1, s2], ["f2", s0..], ..]], ...
        # ]
        remotefiles = []
        opts = f"{'R' in opts},{'l' in opts}"
        listing = self.eval_json(f'_helper.ls_dirs({list(dir_list)},{opts})')
        listing.sort(key=lambda d: d[0])  # Sort by directory pathname
        for dir, file_list in listing:
            # sort each directory listing by filename
            file_list.sort(key=lambda f: f[0])
        remotefiles = (  # Convert to lists of RemotePath objects
            (dir, (RemotePath(f[0]).set_modes(f[1:]) for f in filelist))
            for dir, filelist in listing)
        return remotefiles

    def ls_dir(self, dir: str) -> Iterable[RemotePath]:
        dir_files = next(iter(self.ls_dirs([dir])))
        return dir_files[1] if dir_files else []

    def ls(
            self,
            filenames:  Filenames,
            opts:       str
            ) -> Iterable[tuple[str, Iterable[RemotePath]]]:
        "Return a list of files on the board."
        filenames = list(filenames)
        filenames.sort
        filelist = list(self.ls_files(filenames))  # We parse this several times
        missing = (f for f in filelist if not f.exists())
        files = (f for f in filelist if f.is_file())
        dirs = ((d.slashify() for d in filelist if d.is_dir())
                if filenames else ['./'])
        for f in missing:
            print(f"ls: cannot access {f.as_posix()!r}: No such file or directory")
        lsdirs = self.ls_dirs(dirs, opts)
        return itertools.chain([('', files)], lsdirs)

    def check_files(
            self,
            cmd:        str,
            filenames:  Filenames,
            dest:       str = "",
            opts:       str = ""
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
                    print(f'%{cmd}: Error: {dest!r} is subfolder of {f!r}')
                    return ([], None)
                if str(f) == dest:
                    print(f'%{cmd}: Error: source is same as dest: {f!r}')
                    return ([], None)
        if dirs and cmd in ["rm", "cp", "get", "put"] and 'r' not in opts:
            print(f"%{cmd}: Error: Can not process dirs (use \"{cmd} -r\"): {dirs}")
            return ([], None)

        return (filelist, dest_f)

    def rm(
            self,
            filenames:  Filenames,
            opts:       str
            ) -> None:
        filelist, _ = self.check_files("rm", filenames, "", opts)

        opts = f"{'v' in opts},{'n' in opts}"
        self.exec(f'_helper.rm({[str(f) for f in filelist]},{opts})', silent=False)

    def mv(
            self,
            filenames:  Filenames,
            dest:       str,
            opts:       str
            ) -> None:
        filelist, dest_f = self.check_files("mv", filenames, dest, opts)
        if not filelist or not dest_f:
            return

        if len(filelist) == 1 and not dest_f.is_dir():
            # First - check for special cases...
            f = filelist[0]
            if not dest_f.exists() or f.is_file():
                if 'v' in opts: print(f"{str(f)} -> {str(dest)}")
                self.exec(f"uos.rename({str(f)!r},{dest!r})")
            else:
                print(f'%mv: Error: Destination must be directory: {dest!r}')
            return

        # Move files to dest which is a directory
        for f in filelist:
            f2 = dest_f / f.name
            if 'v' in opts: print(f"{str(f)} -> {str(f2)}")
            self.exec(f'uos.rename({str(f)!r},{str(f2)!r})')

    def cp(
            self,
            filenames:  Filenames,
            dest:       str,
            opts:       str
            ) -> None:
        'Copy files and directories on the micropython board.'
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
                    f'_helper.cp_file({files[0]!r},{dest!r},{opts})',
                    silent=False)
                return
            elif dirs and not dest_f.exists():
                # cp dir1 dir2 (where dir2 does not exist)
                self.exec(
                    f'_helper.cp_dir({dirs[0]!r},{dest + "/"!r},{opts})',
                    silent=False)
                return

        if not dest_f.is_dir():
            print(f"%cp: Destination must be a directory: {dest}")
            return

        self.exec(
            f'_helper.cp({files},{dirs},{dest + "/"!r},{opts})',
            silent=False)

    def get_file(
            self,
            filename:   PathLike,
            dest:       PathLike,
            verbose:    bool = False,
            dry_run:    bool = False
            ) -> None:
        'Copy a file "filename" from the board to the local "dest" folder.'
        if verbose: print(str(dest))
        if not dry_run:
            with self.raw_repl(("get_file", filename, dest)):
                self.pyb.fs_get(str(filename), str(dest))

    def get_dir(
            self,
            dir:        PathLike,
            dest:       PathLike,
            verbose:    bool,
            dry_run:    bool
            ) -> None:
        'Recursively copy a directory from the micropython board.'
        # dir is subdirectory name for recursive
        base: Optional[Path] = None
        for subdir, filelist in self.ls([str(dir)], '-R'):
            srcdir = Path(subdir)
            # First non-empty subdir is base of a recursive listing
            if subdir and base is None:
                base = Path(subdir).parent
            # Destination subdir is dest + relative path from dir to base
            destdir = (
                dest / Path(subdir).relative_to(base)) if base else Path()
            if not destdir.is_dir():
                if verbose: print(str(destdir))
                if not dry_run:
                    os.mkdir(destdir)
            for f in filelist:
                f1 = srcdir / f.name
                f2 = destdir / f.name
                if f.is_file():
                    self.get_file(str(f1), str(f2), verbose, dry_run)

    def get(
            self,
            filenames:  Filenames,
            dest:       PathLike,
            opts:       str = ''
            ) -> None:
        'Copy files and directories from the board to a local folder:'
        dest = Path(dest)
        verbose = 'v' in opts
        dry_run = 'n' in opts
        recursive = 'r' in opts
        filenames = list(filenames)
        if len(filenames) == 1 and not dest.is_dir():
            for f in filenames:
                self.get_file(f, str(dest), verbose, dry_run)
            return
        if not dest.is_dir():
            print('get: Destination directory does not exist:', dest)
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
                            f'use "-r" to copy directories.')
                elif not file.exists():
                    print(f'{str(file)}: No such file.')

    def put_file(
            self,
            filename:   PathLike,
            dest:       PathLike,
            verbose:    bool = False,
            dry_run:    bool = False
            ) -> None:
        'Copy a local file "filename" to the "dest" folder on the board.'
        with self.raw_repl():
            if verbose: print(str(dest))
            if not dry_run:
                self.pyb.fs_put(str(filename), str(dest))

    def put_dir(
            self,
            dir:        Path,
            dest:       RemotePath,
            verbose:    bool = False,
            dry_run:    bool = False
            ) -> None:
        'Recursively copy a directory to the micropython board.'
        base = dir.parent
        # Destination subdir is dest + basename of file
        destdir = dest / dir.name

        for subdirname, _, files in os.walk(dir):
            subdir = Path(subdirname)
            # Dest subdir is dest + relative path from dir to base
            destdir = dest / subdir.relative_to(base)
            if not dry_run:
                d = list(self.ls_files([str(destdir)]))[0]
                if not d.is_dir():
                    self.mkdir(str(destdir))
            for f in files:
                f1, f2 = subdir / f, destdir / f
                self.put_file(f1, f2, verbose, dry_run)

    def put(
            self,
            filenames:  Filenames,
            dest:       PathLike,
            opts:       str = ''
            ) -> None:
        "Copy local files to the current folder on the board."
        dest = Path(dest)
        verbose = 'v' in opts
        dry_run = 'n' in opts
        recursive = 'r' in opts
        filenames = list(filenames)
        with self.raw_repl():
            dest = list(self.ls_files([str(dest)]))[0]
            if len(filenames) == 1 and not dest.is_dir():
                for f in filenames:
                    self.put_file(f, dest, verbose, dry_run)
                return
            if not dest.is_dir():
                print('get: Destination directory does not exist:', dest)
                return
            for filename in filenames:
                file = Path(filename)
                if not file.is_dir():
                    f2 = dest / file.name
                    self.put_file(file, f2, verbose, dry_run)
                elif recursive:  # file is a directory
                    self.put_dir(file, dest, verbose, dry_run)
                else:
                    print(
                        f'put: skipping "{str(file)}", '
                        f'use "-r" to copy directories.')

    def df(
            self,
            dirs:  Filenames
            ) -> Sequence[tuple[str, int, int, int]]:
        ret: list[tuple[str, int, int, int]] = []
        for dir in (dirs or ['/']):
            _, bsz, tot, free, *_ = self.eval_json(
                f'print(list(uos.statvfs("{dir}")))')
            ret.append((dir, tot * bsz, (tot - free) * bsz, free * bsz))
        return ret

    def gc(self) -> tuple[int, int]:
        before, after = self.eval_json(
            'from gc import mem_free,collect;'
            '_b=mem_free();collect();print([_b,mem_free()])')
        return (int(before), int(after))

    def get_localtime(self) -> Sequence[int]:
        return [
            int(i) for i in
            self.eval_json('import utime;print(list(utime.localtime()))')]
