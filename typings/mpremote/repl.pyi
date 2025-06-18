from typing import Callable

from .console import ConsolePosix, ConsoleWindows
from .main import State

Writer = Callable[[bytes], None]  # A type alias for console write functions

def do_repl_main_loop(
    state: State,
    console_in: ConsolePosix | ConsoleWindows,
    console_out_write: Writer,
    *,
    escape_non_printable: bool,
    code_to_inject: bytes,
    file_to_inject: str,
) -> None:
    "Main loop for the REPL, reading from the console and writing to the device."
    ...
