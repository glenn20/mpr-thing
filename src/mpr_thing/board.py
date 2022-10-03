"""Provides the "Board" class which is a wrapper around the
PyBoardExtended interface to a micropython board (from the mpremote tool).
"""
# Copyright (c) 2021 @glenn20
# MIT License

# For python<3.10: Allow method type annotations to reference enclosing class
from __future__ import annotations

import os, re
from pathlib import Path
from typing import Any, Sequence, Iterable, Callable, Optional

from mpremote.pyboard import stdout_write_bytes
from mpremote.pyboardextended import PyboardExtended

from .catcher import catcher, last_exception, raw_repl as real_raw_repl
from .remote_path import RemotePath

# Type aliases
Writer    = Callable[[bytes], None]     # Type of the console write functions
PathLike  = str | os.PathLike           # Accepts str or Pathlib for filenames
Filenames = Iterable[str]               # Accept single filenames as file list


# A collection of helper functions for file listings and filename completion
# to be uploaded to the micropython board and processed on the local host.
class Board:
    """A wrapper for the PyboardExtended class from the mpremote tool.
    Provides convenience methods and wrappers for filesystem operations
    on a micropython board.
    """
    # The helper code which runs on the micropython board
    cmd_hook_code = b"Override this with contents of 'board/cmd_helper.py'."

    # Apply basic compression on hook code - (from mpremote tool).
    HookSubsType = Sequence[tuple[bytes, bytes, dict[str, int]]]
    hook_subs: HookSubsType = [
        (b" *#.*$",          b"",     {'flags': re.MULTILINE}),
        (b"    ",            b" ",    {}),
        (br"([,;])  *",      br"\1",  {}),
        (br"  *([=+-])  *",  br"\1",  {}),
    ]

    compressed = False

    @staticmethod
    def do_hook_subs(subs: HookSubsType, code: bytes) -> bytes:
        'Apply compression techniques to the code before uploading to board.'
        for sub in subs:
            a, b, flags = sub
            code = re.sub(a, b, code, **flags)
        return code

    def __init__(self, pyb: PyboardExtended, writer: Writer) -> None:
        """Construct a "Board" instance.

        Args:
            pyb: An instance of the PyboardExtended class from mpremote tool.
            writer: A function to print output from the micropython board.
        """
        self.pyb = pyb
        self.writer = writer
        self.default_depth = 40   # Max recursion depth for cp(), rm()

        # Load the helper code to install on the micropython board.
        # The helper code is "board/cmd_helper.py" in the module directory.
        micropy_file = Path(__file__).parent / 'board' / 'cmd_helper.py'
        with open(micropy_file, 'rb') as f:
            self.cmd_hook_code = f.read()

        if not Board.compressed:
            Board.compressed = True
            self.cmd_hook_code = (
                Board.do_hook_subs(
                    Board.hook_subs, self.cmd_hook_code))

    def device_name(self) -> str:
        'Get the name of the device connected to the micropython board.'
        name = ''
        if isinstance(self.pyb, PyboardExtended):
            name = self.pyb.device_name
        return name

    def load_hooks(self) -> None:
        'Load the __helper class and methods onto the micropython board.'
        self.exec(self.cmd_hook_code)

    def write(self, response: bytes | str) -> None:
        'Call the console writer for output (convert "str" to "bytes").'
        if response:
            if not isinstance(response, bytes):
                response = bytes(response, 'utf-8')
            self.writer(response)

    def raw_repl(self, silent: bool = False) -> Any:
        'Return a context manager for the micropython raw repl.'
        return real_raw_repl(self.pyb, self.write, silent=silent)

    # Execute stuff on the micropython board
    def exec_(
            self,
            code:       bytes | str,
            reader:     Optional[Writer] = None,
            silent:     bool = False,
            ) -> bytes:
        'Execute some code on the micropython board.'
        response: bytes = b''
        with self.raw_repl(silent=silent):
            response = self.pyb.exec_(code, reader)
        return response

    def eval(
            self,
            code:       str,
            silent:     bool = False,
            ) -> Any:
        # TODO: Use json for return values from board - for safety
        response = self.exec_(code, silent=silent)
        return eval(response)

    def exec(self, code: bytes | str) -> None:
        self.exec_(code, stdout_write_bytes)

    def complete(self, word: str) -> list[str]:
        'Complete the python name on the board.'
        words = [None] + word.rsplit('.', 1)  # Split module and class names
        completions: list[str] = self.eval(f'_helper.complete({words[-2]}, "{words[-1]}")')
        return completions

    def cat(self, filename: str) -> None:
        'List the contents of the file "filename" on the board.'
        with self.raw_repl():
            self.pyb.fs_cat(filename)

    def touch(self, filename: str) -> None:
        self.exec('open("{}", "a").close()'.format(filename))

    def mv(
            self,
            filenames:  Filenames,
            dest:       str,
            opts:       str
            ) -> None:
        if isinstance(filenames, str):
            filenames = [filenames]
        self.exec('_helper.mv({},"{}","{}")'.format(
            [f.rstrip('/') if f != '/' else f for f in filenames],
            dest.rstrip('/') if dest != '/' else dest,
            opts))

    def rm(
            self,
            filenames:  Filenames,
            opts:       str
            ) -> None:
        if isinstance(filenames, str):
            filenames = [filenames]
        self.exec('_helper.rm({},"{}")'.format(
            [f.rstrip('/') if f != '/' else f for f in filenames],
            opts))

    def cd(self, filename: str) -> None:
        self.exec('uos.chdir({})'.format(repr(filename)))

    def pwd(self) -> str:
        pwd: str = self.eval('print(repr(uos.getcwd()))')
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
        if isinstance(self.pyb, PyboardExtended):
            with self.raw_repl():
                self.pyb.mount_local(path, 'l' in opts)

    def umount(self) -> None:
        'Unmount any Virtual Filesystem mounted at "/remote" on the board.'
        # Must chdir before umount or bad things happen.
        self.exec('uos.getcwd().startswith("/remote") and uos.chdir("/")')
        if isinstance(self.pyb, PyboardExtended):
            with self.raw_repl():
                self.pyb.umount_local()

    def ls_files(
            self,
            filenames:  Filenames
            ) -> Iterable[RemotePath]:
        'Return a list of files (RemotePath) on board for list of filenames.'
        with catcher(self.write, silent=True):
            ls: list[tuple[str, Optional[tuple[int, int, int]]]]
            ls = self.eval(f'_helper.ls_files({filenames})', False)
            return [
                RemotePath(f).set_modes(stat, exists=True)
                if stat else
                RemotePath(f).set_exists(False)
                for f, stat in ls]

    def ls_dirs(
            self,
            dir_list:   list[str],
            opts:       str = ""
            ) -> list[tuple[str, list[RemotePath]]]:
        """Return a listing of files in directories on the board.
        Takes a list of directory pathnames and a listing options string.
        Returns a list: [(dirname, [Path1, Path2, Path3..]), ...]
        """
        remotefiles = []
        with catcher(self.write):
            # [("dir1", [["file1", mode, size, ..], ["file2", ...]), (..), ...]
            listing: list[tuple[str, list[tuple[str, int, int, int]]]]
            listing = self.eval(f'_helper.ls_dirs({dir_list},"{opts}")', False)
            listing.sort(key=lambda item: item[0])  # Sort by directory pathname
            for dir, file_list in listing:
                # sort each directory listing by filename
                file_list.sort(key=lambda item: item[0])
            remotefiles = [  # Convert to lists of RemotePath objects
                (dir, [RemotePath(dir, f[0]).set_modes(f[1:]) for f in file_list])
                for dir, file_list in listing]
        if last_exception:
            print('ls_dir(): list directory \'{}\' failed.'.format(dir_list))
            print(last_exception)
            return []
        return remotefiles

    def ls_dir(self, dir: str) -> list[RemotePath]:
        dir_files = self.ls_dirs([dir])
        return dir_files[0][1] if dir_files else []

    def ls(
            self,
            filenames:  Filenames,
            opts:       str
            ) -> Iterable[tuple[str, list[RemotePath]]]:
        "Return a list of files on the board."
        filenames = [filenames] if isinstance(filenames, str) else list(filenames)
        filenames.sort
        filelist = self.ls_files(filenames)
        missing = [f for f in filelist if not f.exists()]
        files = [f for f in filelist if f.is_file()]
        dirs = ([d for d in filelist if d.is_dir()]
                if filenames else [RemotePath('.')])
        for f in missing:
            print(f"ls: cannot access {f.as_posix()!r}: No such file or directory")
        x = [('', files)]
        y = [d.as_posix() if d.as_posix() != "" else "." for d in dirs]
        lsdirs = self.ls_dirs(y, opts)
        return x + lsdirs

    def cp(
            self,
            filenames:  Filenames,
            dest:       str,
            opts:       str
            ) -> None:
        'Copy files and directories on the micropython board.'
        if isinstance(filenames, str):
            filenames = [filenames]
        files = list(self.ls_files([*filenames, dest]))
        dest_f = files.pop()
        if len(files) == 1:
            # First - check for some special cases...
            f = files[0]
            if str(f) == str(dest_f):
                print('%cp: Skipping: source is same as dest:', files[0])
                return
            elif f.is_file() and (dest_f.is_file() or not dest_f.exists()):
                # cp file1 file2
                self.exec(
                    '_helper.cp_file("{}","{}","{}")'.format(
                        str(f), str(dest_f), opts))
                return
            elif f.is_dir() and not dest_f.exists():
                # cp dir1 dir2 (where dir2 does not exist)
                if 'v' in opts: print(str(dest))
                if 'n' not in opts: self.mkdir(str(dest))
                self.exec(
                    '_helper.cp_dir("{}","{}","{}")'.format(
                        str(f) + '/.',  # Ugly hack to make it work
                        str(dest_f), opts))
                return

        # Copy the files and directories to dest
        self.exec(
            '_helper.cp({},"{}","{}")'.format(
                [str(f) for f in files], str(dest_f), opts))

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
            with self.raw_repl():
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
            # First non-empty subdir is base of a recursive listing
            base = Path(subdir).parent if subdir and base is None else base
            # Destination subdir is dest + relative path from dir to base
            destdir = (
                dest / Path(subdir).relative_to(base)) if base else Path()
            if not destdir.is_dir():
                if verbose: print(str(destdir))
                if not dry_run:
                    os.mkdir(destdir)
            for f in filelist:
                f2 = destdir / f.name
                if f.is_file():
                    self.get_file(str(f), str(f2), verbose, dry_run)

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
        if isinstance(filenames, str):
            filenames = [filenames]
        filenames = list(filenames)
        if len(filenames) == 1 and not dest.is_dir():
            self.get_file(filenames[0], str(dest), verbose, dry_run)
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
                            'get: skipping "{}", use "-r" to copy directories.'
                            .format(str(file)))
                elif not file.exists():
                    print('{}: No such file.'.format(str(file)))

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
        if isinstance(filenames, str):
            filenames = [filenames]
        filenames = list(filenames)
        with self.raw_repl():
            dest = list(self.ls_files([str(dest)]))[0]
            if len(filenames) == 1 and not dest.is_dir():
                self.put_file(filenames[0], dest, verbose, dry_run)
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
                        'put: skipping "{}", use "-r" to copy directories.'
                        .format(str(file)))
