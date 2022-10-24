"""Provides the "RemotePath" class which is a wrapper around the
PyBoardExtended interface to a micropython board (from the mpremote tool).
"""
# Copyright (c) 2021 @glenn20
# MIT License

# For python<3.10: Allow method type annotations to reference enclosing class
from __future__ import annotations
from errno import ENOENT

import os
from pathlib import PosixPath, Path
from contextlib import contextmanager
from typing import Sequence, Any, Generator


class RemoteDirEntry:
    def __init__(self, scandir: str, name: str, stat: os.stat_result):
        self.name = name
        self.path = os.path.join(scandir, name)
        self.stat = stat

    def


@contextmanager
def scandir(path: RemotePath) -> Generator[RemoteDirEntry, None, None]:

    yield (RemoteDirEntry(f) for f in )

    return None


# Paths on the board are always Posix paths even if local host is Windows.
class RemotePath(PosixPath):
    'A Pathlib compatible class to hold details of files on the board.'

    epoch_offset: int = 0
    _board: Any = None
    _stat_cache: dict[str, os.stat_result] = {}
    _dir_cache: dict[str, list[str]] = {}
    _cwd: RemotePath | None = None

    @classmethod
    def flush_cache(cls) -> None:
        cls._stat_cache = {}
        cls._dir_cache = {}
        cls._cwd = None

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
        self._stat_cache[self.absolute().as_posix()] = s
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
        name = self.absolute().as_posix()
        return self._stat_cache.get(name) or self.board_stat(name)

    def slashify(self) -> str:
        name = self.as_posix()
        return name + "/" if self.is_dir() and name != "/" else name

    def __repr__(self) -> str:
        return f"RemotePath({self.name!r}, {self.modes()})"

    def iterdir(self) -> Generator[RemotePath, None, None]:
        name = self.absolute().as_posix()
        filenames = self._dir_cache.get(name)
        if filenames:
            yield from (RemotePath(self.as_posix(), f) for f in filenames)
        else:
            opts = "-lR"
            filelist: list[tuple[str, int, int, int]] = \
                list(self._board.eval_json(
                    f'_helper.ls_dirs({[name]},{opts})'))[:1]
            self._dir_cache[name] = [f[0] for f in filelist]
            yield from (RemotePath(self.as_posix(), f[0]).set_modes(f[1:]) for f in filelist)

    def _scandir(self):
        x = os.DirEntry()
        return os.scandir(self)

    def resolve(self, strict: bool = False) -> RemotePath:
        return self.absolute()

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
