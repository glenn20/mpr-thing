"""Context managers for handling the micropython "raw_repl" and
handing exceptions.
"""
# MIT License
# Copyright (c) 2021 @glenn20
#

from traceback import print_exc as traceback_print_exc
from mpremote.pyboard import PyboardError, Pyboard
from mpremote.pyboardextended import PyboardExtended
from typing import (Any, Callable, Optional, Union)

Writer = Callable[[bytes], None]  # A type alias for console write functions


# A context manager to catch exceptions from pyboard and others
class catcher:
    """Catch and report exceptions commonly raised by the mpr-thing tool.
    Eg.
        from catcher import catcher
        with catcher(write_fn):
            id = board.eval("print(unique_id())")"""
    # Save the last exception for use outside the context manager
    exception: Optional[BaseException] = None

    def __init__(self, write_fn: Writer, silent: bool = False):
        """[summary]

        Args:
            write_fn: A function (taking bytes) to print exception messages.
            silent=False: Suppress exception reports.
        """
        self.write_fn   = write_fn
        self.silent     = silent

    def __enter__(self) -> Any:
        catcher.exception = None

    def __exit__(self, exc_type: Any, value: Exception, tr: Any) -> bool:
        if exc_type == KeyboardInterrupt:
            catcher.exception = value
            return False
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


# A context manager for the raw_repl - not re-entrant
class raw_repl():
    """Enter the raw_repl on the micropython board and trap and report
    any PyboardError exceptions raised.

    Eg:
        from catcher import raw_repl
        with raw_repl(pyboard, write_fn, reraise=True):
            board.exec("machine.Pin(4).value(1)")
    """
    def __init__(
            self,
            pyb:        Union[PyboardExtended, Pyboard],
            write_fn:   Writer,
            soft_reset: bool = False,
            silent:     bool = False,
            reraise:    bool = False
            ):
        """Constructor

        Args:
            pyb: An instance of the PyboardExtended class from mpremote
            write_fn: A function (taking bytes) to print exception messages.
            soft_reset=False: Reset micropython before entering raw repl.
            silent=False: Suppress exception reports.
            reraise=False: Re-raise a PyboardError exception after reporting.
        """
        self.pyb            = pyb
        self.write_fn       = write_fn
        self.soft_reset     = soft_reset
        self.silent         = silent
        self.reraise        = reraise
        self.restore_repl   = False

    def __enter__(self) -> Union[PyboardExtended, Pyboard]:
        # We can nest raw_repl() managers - only enter raw repl if necessary
        if not self.pyb.in_raw_repl:
            self.restore_repl = True
            try:
                self.pyb.enter_raw_repl(self.soft_reset)
            except Exception as err:
                self.restore_repl = False
                self.pyb.exit_raw_repl()
                self.pyb.read_until(4, b">>> ")
                print(err)
                raise err
        return self.pyb

    def __exit__(
            self, exc_type: Any, value: Exception, traceback: Any) -> bool:
        # Only exit the raw_repl if we entered it with this instance
        if self.restore_repl and self.pyb.in_raw_repl:
            if exc_type == KeyboardInterrupt:
                # ctrl-C twice: interrupt any running program
                self.pyb.serial.write(b"\r\x03\x03")
            self.pyb.exit_raw_repl()
            self.pyb.read_until(4, b">>> ")
        if exc_type == PyboardError:
            catcher.exception = value
            if not self.silent:
                self.write_fn(b"PyboardError: ")
                if len(value.args) == 3:    # Raised by Pyboard.exec_()
                    self.write_fn(value.args[1])
                    self.write_fn(value.args[2])
                else:           # Others just include a single message
                    self.write_fn(value.args[0].encode())
            return self.reraise
        return False
