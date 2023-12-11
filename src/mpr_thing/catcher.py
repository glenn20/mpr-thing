"""Context managers for handling the micropython "raw_repl" and
handing exceptions.
"""
# MIT License
# Copyright (c) 2021 @glenn20
#

# For python<3.10: Allow type1 | type2 instead of Union[type1, type2]
from __future__ import annotations

from typing import Any, Callable

Writer = Callable[[bytes], None]  # A type alias for console write functions


# A context manager to catch exceptions from pyboard and others
class catcher:
    """Catch and report exceptions commonly raised by the mpr-thing tool.
    Eg.
        from catcher import catcher
        with catcher():
            id = board.eval("print(unique_id())")"""

    nested_depth = 0

    def __enter__(self):
        catcher.nested_depth += 1
        return self

    def __exit__(self, exc_type: Any, exc_value: Any, traceback: Any) -> bool:
        catcher.nested_depth -= 1
        if exc_type is KeyboardInterrupt:
            print("Keyboard Interrupt.")
            if catcher.nested_depth > 0:
                return False  # Propagate the exception
        if exc_type in (OSError, FileNotFoundError):
            print(f"{exc_type.__name__}: {exc_value}")
        elif exc_type is Exception:
            print("Error:: ", end="")
            print(f"{exc_type.__name__}: {exc_value}")
            print(traceback)
        return True
