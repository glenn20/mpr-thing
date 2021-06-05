
# Copyright (c) 2021 @glenn20

# MIT License

# vscode-fold=2

from traceback import print_exc as traceback_print_exc
from mpremote.pyboard import PyboardError
from mpremote.pyboardextended import PyboardExtended
from typing import (Any, Callable, Optional)

Writer = Callable[[bytes], None]  # A type alias for console write functions


# A context manager to catch exceptions from pyboard and others
class catcher:
    # Save the last exception for use outside the context manager
    exception: Optional[BaseException] = None

    def __init__(self, write_fn: Writer, silent: bool = False):
        self.write_fn = write_fn
        self.silent = silent
        catcher.exception = None

    def __enter__(self) -> Any:
        catcher.exception = None
        pass

    def __exit__(
            self, exc_type: Any, value: Exception, tr: Any) -> bool:
        catcher.exception = None
        if exc_type == PyboardError:
            catcher.exception = value
            if not self.silent:
                self.write_fn(b"PyboardError: ")
                if len(value.args) == 3:    # Raised by Pyboard.exec_()
                    self.write_fn(value.args[1])
                    self.write_fn(value.args[2])
                else:           # Others just include a single message
                    self.write_fn(value.args[0].encode())
        elif exc_type == KeyboardInterrupt:
            catcher.exception = value
        elif exc_type in (OSError, FileNotFoundError):
            if not self.silent:
                self.write_fn("{}: {}\r\n".format(
                    exc_type.__name__, value).encode())
        elif value is not None:
            catcher.exception = value
            if not self.silent:
                self.write_fn(b"Error:: ")
                for arg in value.args:
                    self.write_fn(bytes("{}".format(arg), 'utf8'))
                traceback_print_exc()
        return True


# A context manager for the raw_repl
class raw_repl(catcher):
    def __init__(
            self,
            pyb: PyboardExtended,
            write_fn: Writer,
            soft_reset: bool = True,
            silent: bool = False):
        self.pyb = pyb
        self.soft_reset = soft_reset
        self.restore_repl = False
        super().__init__(write_fn, silent)

    def __enter__(self) -> PyboardExtended:
        super().__enter__()
        # We can nest raw_repl()s - only enter the raw repl if necessary
        if not self.pyb.in_raw_repl:
            self.restore_repl = True
            self.pyb.enter_raw_repl(self.soft_reset)
        return self.pyb

    def __exit__(
            self, exc_type: Any, value: Exception, traceback: Any) -> bool:
        # Only exit the raw_repl if we entered it with this context manager
        if self.restore_repl and self.pyb.in_raw_repl:
            self.pyb.exit_raw_repl()
            self.pyb.read_until(4, b">>> ")
        return super().__exit__(exc_type, value, traceback)
