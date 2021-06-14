
# Copyright (c) 2021 @glenn20
# MIT License

# vscode-fold=2

import os, re
from pathlib import PurePosixPath, Path
from typing import (
    Any, Dict, Sequence, Union, Iterable, Callable, Optional, List, Tuple)

from mpremote.pyboard import stdout_write_bytes as pyboard_stdout_write_bytes
from mpremote.pyboardextended import PyboardExtended
from mpremote.main import execbuffer

from .catcher import catcher, raw_repl

Writer = Callable[[bytes], None]  # A type alias for console write functions
PathLike = Union[str, os.PathLike]


# A Pathlib-compatible class to hold details on files from the board
# Paths on the board are always Posix paths even if local host is Windows
class RemotePath(PurePosixPath):
    'A Path class with file status attributes attached.'
    def __new__(cls, *args: str, **_: Dict[str, Any]) -> 'RemotePath':
        # Need this because PurePath.__new__() does not take kwargs and
        # initialises the instances in __new__() not __init__()
        return super().__new__(cls, *args)

    def __init__(
            self, *args: str,
            stat: Sequence[int] = [],
            exists: bool = True) -> None:
        self.mode    = stat[0] if stat else 0
        self.size    = stat[1] if stat[1:] else 0
        self.mtime   = stat[2] if stat[2:] else 0
        self._exists = exists

    def is_dir(self) -> bool:
        return self._exists and ((self.mode & 0x4000) != 0)

    def is_file(self) -> bool:
        return self._exists and not ((self.mode & 0x4000) != 0)

    def exists(self) -> bool:
        return self._exists


# A collection of helper functions for file listings and filename completion
# to be uploaded to the micropython board and processed on the local host.
class Board:
    # The helper code which runs on the micropython board
    cmd_hook_code = b"from cmd_helper import _helper"

    # Apply basic compression on hook code - (from mpremote tool).
    HookSubsType = List[Tuple[bytes, bytes, Dict[str, int]]]
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

    def __init__(
            self,
            pyb:    PyboardExtended,
            writer: Writer) -> None:
        self.pyb = pyb
        self.writer = writer
        self.default_depth = 40   # Max recursion depth for cp(), rm()

        micropy_file = Path(__file__).parent / 'board' / 'cmd_helper.py'
        with open(micropy_file, 'rb') as f:
            self.cmd_hook_code = f.read()

        if not Board.compressed:
            Board.compressed = True
            self.cmd_hook_code = (
                Board.do_hook_subs(
                    Board.hook_subs, self.cmd_hook_code))

    def load_hooks(self) -> None:
        self.exec(self.cmd_hook_code)

    def write(self, response: bytes) -> None:
        'Call the console writer for output (convert "str" to "bytes").'
        if response:
            if not isinstance(response, bytes):
                response = bytes(response, 'utf-8')
            self.writer(response)

    # Execute stuff on the micropython board
    def exec_(
            self,
            code:       Union[str, bytes],
            reader:     Optional[Writer] = None,
            silent:     bool = False,
            follow:     bool = True,
            ) -> bytes:
        'Execute some code on the micropython board.'
        if follow:
            response: bytes = b''
            with raw_repl(self.pyb, self.write, silent=silent):
                response = self.pyb.exec_(code, reader)
            return response
        else:
            with raw_repl(self.pyb, self.write, silent=silent):
                _ = execbuffer(self.pyb, code, follow=False)
            return b''

    def eval(self, code: str, silent: bool = False) -> Any:
        # TODO: Use json for return values from board - for safety
        response = self.exec_(code, silent=silent)
        return eval(response)

    def exec(self, code: Union[str, bytes], follow: bool = True) -> None:
        self.exec_(code, pyboard_stdout_write_bytes, follow=follow)

    def cat(self, filename: str) -> None:
        'List the contents of the file "filename" on the board.'
        with raw_repl(self.pyb, self.write):
            self.pyb.fs_cat(filename)

    def touch(self, filename: str) -> None:
        self.exec(
            'open("{}", "a").close()'.format(filename))

    def mv(self, filenames: Iterable[str], dest: str, opts: str) -> None:
        self.exec('_helper.mv({}, "{}", {})'.format(
            [f.rstrip('/') if f != '/' else f for f in filenames],
            dest.rstrip('/') if dest != '/' else dest,
            'v' in opts))

    def cp(self, filenames: Iterable[str], dest: str, opts: str) -> None:
        self.exec('_helper.cp({}, "{}", {}, {})'.format(
            [f.rstrip('/') if f != '/' else f for f in filenames],
            dest.rstrip('/') if dest != '/' else dest,
            self.default_depth if 'r' in opts else 0,
            'v' in opts))

    def rm(self, filenames: Iterable[str], opts: str) -> None:
        self.exec('_helper.rm({}, {}, {})'.format(
            [f.rstrip('/') if f != '/' else f for f in filenames],
            self.default_depth if 'r' in opts else 0,
            'v' in opts))

    def cd(self, filename: str) -> None:
        self.exec('uos.chdir({})'.format(repr(filename)))

    def pwd(self) -> str:
        pwd: str = self.eval('print(repr(uos.getcwd()))')
        return pwd

    def mkdir(self, filename: str) -> None:
        with raw_repl(self.pyb, self.write):
            self.pyb.fs_mkdir(filename)

    def rmdir(self, filename: str) -> None:
        with raw_repl(self.pyb, self.write):
            self.pyb.fs_rmdir(filename)

    def mount(self, directory: str) -> None:
        path = os.path.realpath(directory)
        if not os.path.isdir(path):
            print("%mount: No such directory:", path)
            return
        with raw_repl(self.pyb, self.write):
            self.pyb.mount_local(path)

    def umount(self) -> None:
        'Unmount any Virtual Filesystem mounted at "/remote" on the board.'
        # Must chdir before umount or bad things happen.
        self.exec('uos.getcwd().startswith("/remote") and uos.chdir("/")')
        with raw_repl(self.pyb, self.write):
            self.pyb.umount_local()

    def ls_files(
            self,
            filenames:  Iterable[str]
            ) -> Iterable[RemotePath]:
        'Return a list of files (RemotePath) on board for list of filenames.'
        files: List[RemotePath] = []
        for f in filenames:
            with catcher(self.write, silent=True):
                mode, size, mtime = self.eval((
                    'try: s=uos.stat("{}");print((s[0],s[6],s[8]))\n'
                    'except: s=None\n').format(f), silent=True)
            files.append(
                RemotePath(f, stat=(mode, size, mtime))
                if not catcher.exception else
                RemotePath(f, exists=False))
        return files

    def ls_dir(
            self,
            dir:    str,
            long:   bool = False
            ) -> Optional[Iterable[RemotePath]]:
        'Return a list of files (RemotePath) on board in "dir".'
        with catcher(self.write, silent=False):
            files = [
                RemotePath(dir, f, stat=stat)
                for f, *stat in self.eval(
                    '_helper.ls("{}",{})'.format(dir, long),
                    silent=True)]
            files.sort(key=lambda f: f.name)
        if catcher.exception:
            print('ls_dir(): list directory \'{}\' failed.'.format(dir))
            print(catcher.exception)
            return None
        return files

    def ls(
            self,
            filenames:  Iterable[str],
            opts:       str
            ) -> Iterable[Tuple[str, Iterable[RemotePath]]]:
        "Return a list of files on the board."
        recursive = 'R' in opts
        long_style = 'l' in opts
        filenames = list(filenames)
        filenames.sort
        files = self.ls_files(filenames)
        yield ('', [f for f in files if f.is_file()])

        dirs = (
            list(d for d in files if d.is_dir()) if filenames else
            [RemotePath('.')])
        for i, dir in enumerate(dirs):
            subfiles = self.ls_dir(str(dir), long_style)
            if subfiles is not None:
                yield (str(dir), subfiles)

                if recursive:     # Recursive listing
                    # As we find subdirs, insert them next in the list
                    for j, subdir in enumerate(
                            d for d in subfiles if d.is_dir()):
                        dirs.insert(i + j + 1, subdir)

    def get_file(self, filename: str, dest: str) -> None:
        'Copy a file "filename" from the board to the local "dest" folder.'
        with raw_repl(self.pyb, self.write):
            self.pyb.fs_get(filename, dest)

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
                    if verbose: print(str(f2))
                    if not dry_run:
                        self.get_file(str(f), str(f2))

    def get(
            self,
            filenames:  Iterable[str],
            dest:       PathLike,
            opts:       str = ''
            ) -> None:
        'Copy files from the board to a local folder:'
        dest      = Path(dest)
        verbose   = 'v' in opts
        dry_run   = 'n' in opts
        recursive = 'r' in opts
        if not dest.is_dir():
            print('get: Destination directory does not exist:', dest)
            return
        for file in self.ls_files(filenames):
            # dir: str, file: RemotePathList (list of full pathnames)
            if not file.is_dir():
                f2 = dest / file.name
                if verbose: print(str(f2))
                if not dry_run: self.get_file(str(file), str(f2))
            elif recursive:  # Is a directory
                self.get_dir(file, dest, verbose, dry_run)
            else:
                print('get: skipping "{}", use "-r" to copy directories.'
                      .format(str(file)))

    def put_file(self, filename: str, dest: str) -> None:
        'Copy a local file "filename" to the "dest" folder on the board.'
        with raw_repl(self.pyb, self.write):
            self.pyb.fs_put(filename, dest)

    def put_dir(
            self,
            dir:        Path,
            dest:       Path,
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
                if verbose: print(str(f2))
                if not dry_run:
                    self.put_file(str(f1), str(f2))

    def put(
            self,
            filenames:  Iterable[str],
            dest:       PathLike,
            opts:       str = ''
            ) -> None:
        "Copy local files to the current folder on the board."
        dest      = Path(dest)
        verbose   = 'v' in opts
        dry_run   = 'n' in opts
        recursive = 'r' in opts
        for filename in filenames:
            file = Path(filename)
            if not file.is_dir():
                f2 = dest / file.name
                if verbose: print(f2)
                if not dry_run:
                    self.put_file(str(file), str(f2))
            elif recursive:
                self.put_dir(self, file, )
            else:
                print('put: skipping "{}", use "-r" to copy directories.'
                      .format(str(file)))
