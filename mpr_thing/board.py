
# Copyright (c) 2021 @glenn20
# MIT License

# vscode-fold=2

import os, re
from pathlib import PurePosixPath, Path
from typing import (
    Any, Dict, Generator, Sequence, Union,
    Iterable, Callable, Optional, List, Tuple)

from mpremote.pyboard import stdout_write_bytes
from mpremote.pyboardextended import PyboardExtended

from .catcher import catcher, raw_repl

Writer = Callable[[bytes], None]  # A type alias for console write functions
PathLike = Union[str, os.PathLike]


# Some helper classes for managing lists of files returned from the board
# Paths on the board are always Posix paths even if local host is Windows
class File(PurePosixPath):
    'A Path class with file status attributes attached.'
    def __new__(cls, *args: str, **_: Dict[str, Any]) -> 'File':
        # Need this because PurePath.__new__() does not take kwargs
        return super().__new__(cls, *args)

    def __init__(
            self, *args: str,
            stat: Sequence[int] = [],
            exists: bool = True) -> None:
        self.mode   = stat[0] if stat else 0
        self.size   = stat[1] if stat[1:] else 0
        self.mtime  = stat[2] if stat[2:] else 0
        self.exists = exists

    def path(self) -> str:
        return str(self)

    def is_dir(self) -> bool:
        return self.exists and ((self.mode & 0x4000) != 0)

    def is_file(self) -> bool:
        return self.exists and not ((self.mode & 0x4000) != 0)


class FileList(List[File]):
    'A list of "File"s: used for file listings from the board.'
    @property
    def files(self) -> Generator[File, None, None]:
        return (f for f in self if f.is_file())

    @property
    def dirs(self) -> Generator[File, None, None]:
        return (f for f in self if f.is_dir())

    @property
    def not_found(self) -> Generator[File, None, None]:
        return (f for f in self if not f.exists)

    def file(self, name: str, dir: str = '') -> File:
        'Return File with "name". Return File(exists=False) if not found.'
        return next(
            (f for f in self if f.name == name),
            File(dir, name, exists=False))

    def is_file(self, name: str) -> bool:
        return any(f for f in self.files if f.name == name)

    def is_dir(self, name: str) -> bool:
        return any(f for f in self.dirs if f.name == name)


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
            code:   Union[str, bytes],
            reader: Optional[Writer] = None,
            silent: bool = False
            ) -> bytes:
        'Execute some code on the micropython board.'
        response: bytes = b''
        with raw_repl(self.pyb, self.write, silent=silent):
            response = self.pyb.exec_(code, reader)
        return response

    def eval(self, code: str, silent: bool = False) -> Any:
        # TODO: Use json for return values from board - for safety
        return eval(self.exec_(code, silent=silent))

    def exec(self, code: Union[str, bytes]) -> None:
        self.exec_(code, stdout_write_bytes)

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
            ) -> FileList:
        'Return a list of File(name, mode, size, mtime) for list of filenames.'
        files = FileList()
        for f in filenames:
            with catcher(self.write, silent=True):
                mode, size, mtime = self.eval((
                    'try: s=uos.stat("{}");print((s[0],s[6],s[8]))\n'
                    'except: s=None\n').format(f), silent=True)
            files.append(
                File(f, stat=(mode, size, mtime)) if not catcher.exception else
                File(f, exists=False))
        return files

    def ls_dir(
            self,
            dir:    str,
            long:   bool = False
            ) -> Optional[FileList]:
        'Return a list of File(name, mode, size, mtime) for files in "dir".'
        with catcher(self.write, silent=True):
            files = FileList([
                File(dir, f, stat=stat)
                for f, *stat in self.eval(
                    '_helper.ls("{}",{})'.format(dir, long),
                    silent=True)])
            files.sort(key=lambda f: f.name)
        if catcher.exception:
            print('ls_dir(): list directory \'{}\' failed.'.format(dir))
            return None
        return files

    def ls(
            self,
            filenames:  Iterable[str],
            opts:       str
            ) -> Generator[Tuple[str, FileList], None, None]:
        "Return a list of files on the board."
        recursive = 'R' in opts
        long_style = 'l' in opts
        filenames = list(filenames)
        filenames.sort
        files = self.ls_files(filenames)
        yield ('', FileList(files.files))

        dirs = list(files.dirs) if filenames else [File('.')]
        for i, dir in enumerate(dirs):
            subfiles = self.ls_dir(str(dir), long_style)
            if subfiles is not None:
                yield (str(dir), subfiles)

                if recursive:     # Recursive listing
                    # As we find subdirs, insert them next in the list
                    for j, subdir in enumerate(subfiles.dirs):
                        dirs.insert(i + j + 1, subdir)

    def get_file(self, filename: str, dest: str) -> None:
        'Copy a file "filename" from the board to the local "dest" folder.'
        with raw_repl(self.pyb, self.write):
            self.pyb.fs_get(filename, dest)

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
            # dir: str, file: FileList (list of full pathnames)
            if not file.is_dir():
                f2 = dest / file.name
                if verbose: print(str(f2))
                if not dry_run: self.get_file(str(file), str(f2))
                continue
            # else file is dir
            if not recursive:
                print('get: skipping "{}", use "-r" to copy directories.'
                      .format(str(file)))
                continue

            # dir is subdirectory name for recursive
            base: Optional[Path] = None
            for dir, filelist in self.ls([str(file)], '-R'):
                # First non-empty dir is base of a recursive listing
                base = Path(dir).parent if dir and base is None else base
                # Destination subdir is dest + relative path from dir to base
                destdir = (
                    dest / Path(dir).relative_to(base)) if base else Path()
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

    def put_file(self, filename: str, dest: str) -> None:
        'Copy a local file "filename" to the "dest" folder on the board.'
        with raw_repl(self.pyb, self.write):
            self.pyb.fs_put(filename, dest)

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
                continue
            if not recursive:
                print('put: skipping "{}", use "-r" to copy directories.'
                      .format(str(file)))
                continue

            base = file.parent
            # Destination subdir is dest + basename of file
            destdir = dest / file.name

            for dirname, _, files in os.walk(file):
                dir = Path(dirname)
                # Dest subdir is dest + relative path from dir to base
                destdir = dest / dir.relative_to(base)
                if not dry_run:
                    d = self.ls_files([str(destdir)])[0]
                    if not d.is_dir():
                        self.mkdir(str(destdir))
                for f in files:
                    f1, f2 = dir / f, destdir / f
                    if verbose: print(str(f2))
                    if not dry_run:
                        self.put_file(str(f1), str(f2))