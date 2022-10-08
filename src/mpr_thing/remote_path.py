"""Provides the "RemotePath" class which is a wrapper around the
PyBoardExtended interface to a micropython board (from the mpremote tool).
"""
# Copyright (c) 2021 @glenn20
# MIT License

# For python<3.10: Allow method type annotations to reference enclosing class
from __future__ import annotations

import stat
from pathlib import PurePosixPath
from typing import Sequence


# Paths on the board are always Posix paths even if local host is Windows.
class RemotePath(PurePosixPath):
    'A Pathlib compatible class to hold details of files on the board.'

    def __init__(self, *args: str) -> None:
        # Note: Path initialises from *args in __new__()!!!
        self.mode    = 0
        self.size    = 0
        self.mtime   = 0
        self._exists = False

    def set_modes(self, stat: Sequence[int]) -> RemotePath:
        """Set the file mode, size and mtime values.

        Args:
            stat: A tuple of ints: (mode, size, mtime)
            exists=True: Flag if the file exists (or not).

        Returns:
            RemotePath: [description]
        """
        self.mode    = stat[0] if stat else 0
        self.size    = stat[1] if stat[1:] else 0
        self.mtime   = stat[2] if stat[2:] else 0
        self._exists = bool(stat)
        return self  # So we can f = RemotePath('/main.py').set_modes(...)

    def set_exists(self, exists: bool) -> RemotePath:
        """Set the existence state of the file."""
        self._exists = exists
        return self

    def is_dir(self) -> bool:
        """Return True if the file is a directory."""
        return self._exists and ((self.mode & stat.S_IFDIR) != 0)

    def is_file(self) -> bool:
        """Return True of the file is a regular file."""
        return self._exists and ((self.mode & stat.S_IFREG) != 0)

    def exists(self) -> bool:
        """Return True if the file exists."""
        return self._exists
