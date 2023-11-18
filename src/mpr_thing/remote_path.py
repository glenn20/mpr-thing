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
    "A Pathlib compatible class to hold details of files on the board."

    epoch_offset: int = 0

    def __init__(self, *_args: str) -> None:
        # Note: Path initialises from *args in __new__()!!!
        self.mode = 0
        self.size = 0
        self.mtime = 0
        self._exists = False

    def set_modes(self, modes: Sequence[int]) -> RemotePath:
        """Set the file mode, size and mtime values."""
        self.mode = modes[0] if modes else 0
        self.size = modes[1] if modes[1:] else -1
        self.mtime = modes[2] + self.epoch_offset if modes[2:] else -1
        self._exists = bool(modes)
        return self  # So we can f = RemotePath('/main.py').set_modes(...)

    def modes(self) -> tuple[int, int, int]:
        return (self.mode, self.size, self.mtime - self.epoch_offset)

    def stat(self) -> tuple[int, int, int, int, int, int, int, int, int, int]:
        return (self.mode, 0, 0, 0, 0, 0, self.size, self.mtime, self.mtime, self.mtime)

    def is_dir(self) -> bool:
        "Return True if the file is a directory."
        return (
            hasattr(self, "_exists")
            and self._exists
            and ((self.mode & stat.S_IFDIR) != 0)
        )

    def is_file(self) -> bool:
        "Return True if the file is a regular file."
        return (
            hasattr(self, "_exists")
            and self._exists
            and ((self.mode & stat.S_IFREG) != 0)
        )

    def exists(self) -> bool:
        "Return True if the file exists."
        return hasattr(self, "_exists") and self._exists

    def __repr__(self) -> str:
        return f"RemotePath({self.name!r}, {[self.mode, self.size, self.mtime]})"
