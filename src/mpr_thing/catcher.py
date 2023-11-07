"""Context managers for handling the micropython "raw_repl" and
handing exceptions.
"""
# MIT License
# Copyright (c) 2021 @glenn20
#

# For python<3.10: Allow type1 | type2 instead of Union[type1, type2]
from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Callable, Generator

from mpremote.transport_serial import SerialTransport, TransportError

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


# A context manager for the raw_repl - not re-entrant
@contextmanager
def raw_repl(
    transport: SerialTransport,
    write_fn: Writer,
    message: Any = None,
    soft_reset: bool = False,
) -> Generator[None, None, None]:
    """Enter the raw_repl on the micropython board and trap and report
    any PyboardError exceptions raised.

    Eg:
        from catcher import raw_repl
        with raw_repl(pyboard, write_fn):
            board.exec("machine.Pin(4).value(1)")
    """
    restore_repl = False
    try:
        # We can nest raw_repl() managers - only enter raw repl if necessary
        if not transport.in_raw_repl:
            restore_repl = True
            transport.enter_raw_repl(soft_reset)
        yield

    except KeyboardInterrupt:
        # ctrl-C twice: interrupt any running program
        print("Interrupting command on board.")
        transport.serial.write(b"\r\x03\x03")
        raise
    except TransportError as exc:
        write_fn(f"TransportError: {message!r}\r\n".encode())
        if len(exc.args) == 3:  # Raised by Pyboard.exec_()
            write_fn(exc.args[1])
            write_fn(exc.args[2])
        else:  # Others just include a single message
            write_fn(exc.args[0].encode())
    except Exception as err:
        raise err
    finally:
        # Only exit the raw_repl if we entered it with this instance
        if restore_repl and transport.in_raw_repl:
            transport.exit_raw_repl()
            transport.read_until(4, b">>> ")
