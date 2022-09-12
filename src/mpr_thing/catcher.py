"""Context managers for handling the micropython "raw_repl" and
handing exceptions.
"""
# MIT License
# Copyright (c) 2021 @glenn20
#

# For python<3.10: Allow type1 | type2 instead of Union[type1, type2]
from __future__ import annotations

from contextlib import contextmanager
from typing import Callable, Optional
from traceback import print_exc

from mpremote.pyboard import PyboardError
from mpremote.pyboardextended import PyboardExtended

Writer = Callable[[bytes], None]  # A type alias for console write functions

# Save the last exception for use outside the context manager
last_exception: Optional[BaseException] = None


# A context manager to catch exceptions from pyboard and others
@contextmanager
def catcher(write_fn: Writer, silent: bool = False):
    """Catch and report exceptions commonly raised by the mpr-thing tool.
    Eg.
        from catcher import catcher
        with catcher(write_fn):
            id = board.eval("print(unique_id())")"""
    global last_exception
    last_exception = None
    try:
        yield "catcher"

    except KeyboardInterrupt as exc:
        last_exception = exc
        raise    # Re-raise the exception
    except (OSError, FileNotFoundError) as exc:
        if not silent:
            write_fn(f"{exc.__class__.__name__}: {exc}\r\n".encode())
    except Exception as exc:
        last_exception = exc
        if not silent:
            write_fn(b"Error:: ")
            for arg in exc.args:
                write_fn(f"{arg}".encode())
            print_exc()


# A context manager for the raw_repl - not re-entrant
@contextmanager
def raw_repl(
    pyb:        PyboardExtended,
    write_fn:   Writer,
    soft_reset: bool = False,
    silent:     bool = False
):
    """Enter the raw_repl on the micropython board and trap and report
    any PyboardError exceptions raised.

    Eg:
        from catcher import raw_repl
        with raw_repl(pyboard, write_fn):
            board.exec("machine.Pin(4).value(1)")
    """
    global last_exception
    restore_repl = False
    try:
        # We can nest raw_repl() managers - only enter raw repl if necessary
        if not pyb.in_raw_repl:
            restore_repl = True
            pyb.enter_raw_repl(soft_reset)
        yield pyb

    except KeyboardInterrupt:
        # ctrl-C twice: interrupt any running program
        pyb.serial.write(b"\r\x03\x03")
    except PyboardError as exc:
        last_exception = exc
        if not silent:
            write_fn(b"PyboardError: ")
            if len(exc.args) == 3:    # Raised by Pyboard.exec_()
                write_fn(exc.args[1])
                write_fn(exc.args[2])
            else:           # Others just include a single message
                write_fn(exc.args[0].encode())
    except Exception as err:
        restore_repl = False
        pyb.exit_raw_repl()
        pyb.read_until(4, b">>> ")
        print(err)
        raise err
    finally:
        # Only exit the raw_repl if we entered it with this instance
        if restore_repl and pyb.in_raw_repl:
            pyb.exit_raw_repl()
            pyb.read_until(4, b">>> ")
