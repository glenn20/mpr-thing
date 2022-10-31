"""Provides the "RemotePath" class which is a wrapper around the
PyBoardExtended interface to a micropython board (from the mpremote tool).
"""
# Copyright (c) 2021 @glenn20
# MIT License

# For python<3.10: Allow method type annotations to reference enclosing class
from __future__ import annotations
from errno import ENOENT

import os
import io
from pathlib import PosixPath, Path, PurePath
from typing import Sequence, Any, Generator


class RemoteDirEntry:
    def __init__(self, path: RemotePath):
        self._path = path

    @property
    def name(self) -> str:
        return self._path.name

    @property
    def path(self) -> str:
        return self._path.as_posix()

    def inode(self) -> int:
        return self._path.stat().st_ino

    def is_dir(self) -> bool:
        return self._path.is_dir()

    def is_file(self) -> bool:
        return self._path.is_file()

    def is_symlink(self) -> bool:
        return self._path.is_symlink()

    def stat(self) -> os.stat_result:
        return self._path.stat()


# Paths on the board are always Posix paths even if local host is Windows.
class RemotePath(PosixPath):
    'A Pathlib compatible class to hold details of files on the board.'

    epoch_offset: int = 0
    _board: Any = None
    _stat_cache: dict[RemotePath, os.stat_result] = {}
    _dir_cache: dict[RemotePath, list[RemotePath]] = {}
    _cwd: RemotePath | None = None
    _chunk_size = 256

    @classmethod
    def flush_cache(cls) -> None:
        cls._stat_cache = {}
        cls._dir_cache = {}
        cls._cwd = None

    @classmethod
    def del_cache(cls, path: RemotePath) -> None:
        try:
            del cls._dir_cache[path.parent]
        except KeyError:
            pass
        if path.is_dir():
            try:
                del cls._dir_cache[path]
            except KeyError:
                pass
        try:
            del cls._stat_cache[path]
        except KeyError:
            pass

    @classmethod
    def cwd(cls) -> RemotePath:
        cls._cwd = (
            cls._cwd or
            cls(cls._board.eval_json('print("\\"{}\\"".format(uos.getcwd()))')))
        return cls._cwd

    @classmethod
    def home(cls) -> RemotePath:
        return cls("/")

    @property
    def mode(self) -> int:
        return self.stat().st_mode

    @property
    def size(self) -> int:
        return self.stat().st_size

    @property
    def mtime(self) -> int:
        return int(self.stat().st_mtime)

    def _set_stat(self, stat: Sequence[int]) -> os.stat_result:
        mode, size, mtime = (list(stat) + [-1, -1, -1])[0:3]
        if mtime >= 0:
            mtime += self.epoch_offset
        s = os.stat_result((mode, 0, 0, 0, 0, 0, size, mtime, mtime, mtime))
        self._stat_cache[self.resolve()] = s
        return s

    def set_modes(self, stat: Sequence[int]) -> RemotePath:
        """Set the file mode, size and mtime values.

        Args:
            stat: A tuple of ints: (mode, size, mtime)
            exists=True: Flag if the file exists (or not).

        Returns:
            RemotePath: [description]
        """
        self._set_stat(stat)
        return self  # So we can f = RemotePath('/main.py').set_modes(...)

    def modes(self) -> tuple[int, int, int]:
        s = self.stat()
        return (s.st_mode, s.st_size, int(s.st_mtime - self.epoch_offset))

    def board_stat(self, name: str) -> os.stat_result:
        if not self._board:
            raise NotImplementedError
        s: list[int] = list(self._board.eval_json(f'_helper.stat({name!r})'))
        print(s)
        if not s:
            raise OSError(ENOENT)
        return self._set_stat(s)

    def stat(self, *, follow_symlinks: bool = True) -> os.stat_result:
        truepath = self.absolute()
        return (
            self._stat_cache.get(truepath) or
            self.board_stat(truepath.as_posix()))

    def slashify(self) -> str:
        name = self.as_posix()
        return name + "/" if self.is_dir() and name != "/" else name

    def __repr__(self) -> str:
        return f"RemotePath({self.name!r}, {self.modes()})"

    def iterdir(self) -> Generator[RemotePath, None, None]:
        truepath = self.resolve()
        filepaths = self._dir_cache.get(truepath)
        if not filepaths:
            opts = "-l"
            filelist: list[tuple[str, int, int, int]] = \
                list(self._board.eval_json(
                    f'_helper.ls_dirs({[truepath.as_posix()]},{opts})'))[:1]
            filepaths = [
                RemotePath(truepath.as_posix(), f[0]).set_modes(f[1:])
                for f in filelist]
            self._dir_cache[truepath] = filepaths
        yield from filepaths

    def _scandir(self) -> Generator[RemoteDirEntry, None, None]:
        yield from (RemoteDirEntry(p) for p in self.iterdir())

    def resolve(self, strict: bool = False) -> RemotePath:
        return RemotePath(os.path.normpath(self)).absolute()

    def samefile(self, other_path: str | bytes | int | Path) -> bool:
        if isinstance(other_path, int):
            raise TypeError("RemotePath.samefile(): Can not create path from int.")
        if isinstance(other_path, str) or isinstance(other_path, bytes):
            p = RemotePath(str(other_path))
        elif not isinstance(other_path, RemotePath):
            return False
        else:
            p = other_path
        return self.resolve() == p.resolve()

    def open(self, mode='r', buffering=-1, encoding=None,  # type: ignore
             errors=None, newline=None) -> io.TextIOWrapper:
        raise NotImplementedError

    def read_bytes(self) -> bytes:
        raise NotImplementedError

    def read_text(self, encoding: str | None = None,
                  errors: str | None = None) -> str:
        raise NotImplementedError

    def write_bytes(self, data: bytes) -> int:
        raise NotImplementedError

    def write_text(
            self, data: str, encoding: str | None = None,
            errors: str | None = None, newline: str | None = None
            ) -> int:
        raise NotImplementedError

    def readlink(self) -> RemotePath:
        raise NotImplementedError("os.readlink() not available on this system")

    def touch(self, mode: int = 0o666, exist_ok: bool = True) -> None:
        self._board.touch(self.as_posix())
        self.del_cache(self)

    def mkdir(self, mode: int = 0o777, parents: bool = False,
              exist_ok: bool = False) -> None:
        self._board.mkdir(self.as_posix())
        self.del_cache(self)

    def chmod(self, mode: int, *, follow_symlinks: bool = True) -> None:
        raise NotImplementedError("os.chmod() not available on this system")

    def lchmod(self, mode: int) -> None:
        raise NotImplementedError("os.lchmod() not available on this system")

    def unlink(self, missing_ok: bool = False) -> None:
        self._board.rm([self.as_posix()], "")
        self.del_cache(self)

    def rmdir(self) -> None:
        self._board.rmdir(self.as_posix())
        self.del_cache(self)

    def lstat(self) -> os.stat_result:
        raise NotImplementedError("os.lstat() not available on this system")

    def rename(self, target: str | PurePath) -> RemotePath:
        self._board.mv([self.as_posix()], target)
        self.del_cache(self)
        return RemotePath(target)

    def replace(self, target: str | PurePath) -> RemotePath:
        # TODO: overwrite target if it exists
        self._board.mv([self.as_posix()], target)
        self.del_cache(self)
        return RemotePath(target)

    def symlink_to(self, target: str | Path,
                   target_is_directory: bool = False) -> None:
        raise NotImplementedError("os.symlink() not available on this system")

    def hardlink_to(self, target: str | Path) -> None:
        raise NotImplementedError("os.link() not available on this system")
