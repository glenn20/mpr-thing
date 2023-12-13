#!/usr/bin/env python3

# MIT License: Copyright (c) 2021 @glenn20

import locale
import re
import select
import time
from typing import Callable

import mpremote.main
from mpremote import repl as mpremote_repl
from mpremote.console import ConsolePosix, ConsoleWindows
from mpremote.main import State
from mpremote.transport_serial import SerialTransport, TransportError
from mpremote_path import Board
from serial import Serial

from .remote_commands import RemoteCmd

Writer = Callable[[bytes], None]  # A type alias for console write functions


def hard_reset(transport: SerialTransport) -> None:
    "Toggle DTR on the serial port to force a hardware reset of the board."
    while hasattr(transport.serial, "orig_serial"):
        transport.serial = transport.serial.orig_serial  # type: ignore
    if isinstance(transport.serial, Serial):
        serial = transport.serial
        if hasattr(serial, "dtr"):
            serial.dtr = not serial.dtr
            time.sleep(0.1)
            serial.dtr = not serial.dtr
        transport.mounted = False


def cursor_column(console_in: ConsolePosix | ConsoleWindows, writer: Writer) -> int:
    "Query the console to get the current cursor column number."
    writer(b"\x1b[6n")  # Query terminal for cursor position
    buf = b""
    for _ in range(10):  # Don't wait forever - just in case
        if isinstance(console_in, ConsolePosix):
            select.select([console_in.infd], [], [], 0.1)
        else:
            # TODO: Windows terminal code is untested - I hope it works...
            for _ in range(10):  # Don't wait forever - just in case
                if console_in.inWaiting():
                    break
                time.sleep(0.01)
        c = console_in.readchar()
        if c is not None:
            buf += c
            if c == b"R":  # Wait for end of escape sequence
                break
    else:
        return -1

    match = re.match(r"^\x1b\[(\d)*;(\d*)R", buf.decode())
    return int(match.groups()[1]) if match else -1


# This is going to override do_repl_main_loop in the mpremote module
# It interprets a "!" or "%" character typed at the start of a line
# at the base python prompt as starting a "magic" command.
def my_do_repl_main_loop(  # noqa: C901 - ignore function is too complex
    state: State,
    console_in: ConsolePosix | ConsoleWindows,
    console_out_write: Writer,
    *,
    escape_non_printable: bool,
    code_to_inject: bytes,
    file_to_inject: str,
) -> None:
    'An overload function for the main repl loop in "mpremote".'

    at_prompt, beginning_of_line, prompt_char_count = False, False, 0
    prompt = b"\n>>> "
    transport: SerialTransport = state.transport  # type: ignore
    remote = RemoteCmd(Board(transport))

    while True:
        console_in.waitchar(transport.serial)
        c = console_in.readchar()
        if c:
            if c in (b"\x1d", b"\x18"):  # ctrl-] or ctrl-x, quit
                break
            elif c == b"\x04":  # ctrl-D
                # do a soft reset and reload the filesystem hook
                transport.write_ctrl_d(console_out_write)
                beginning_of_line = True
                remote.reset()
            elif c == b"\x12":  # ctrl-R
                # Toggle DTR (hard reset) and reload the filesystem hook
                hard_reset(transport)
                beginning_of_line = True
                remote.reset()
            elif c == b"\x0a" and code_to_inject is not None:
                transport.serial.write(code_to_inject)  # ctrl-j, inject code
            elif c == b"\x0b" and file_to_inject is not None:
                console_out_write(bytes(f"Injecting {file_to_inject}\r\n", "utf8"))
                transport.enter_raw_repl(soft_reset=False)
                with open(file_to_inject, "rb") as f:
                    pyfile = f.read()
                try:
                    transport.exec_raw_no_follow(pyfile)
                except TransportError as er:
                    console_out_write(b"Error:\r\n")
                    console_out_write(repr(er).encode())
                transport.exit_raw_repl()
                beginning_of_line = True
            elif (
                c in b"%!"
                and at_prompt  # Magic sequence if at start of line
                and (
                    beginning_of_line
                    or cursor_column(console_in, console_out_write) == len(prompt)
                )
            ):
                console_out_write(b"\x1b[2K")  # Clear other chars on line
                console_in.exit()
                try:
                    remote.run(c)
                finally:
                    console_in.enter()
                if c == b"!":
                    transport.serial.write(b"\x0d")  # Force another prompt
                beginning_of_line = True
            elif c == b"\x0d":  # ctrl-m: carriage return
                transport.serial.write(c)
                beginning_of_line = True
            else:
                transport.serial.write(c)
                beginning_of_line = False

        n = 0
        try:
            n = transport.serial.inWaiting()  # type: ignore
        except OSError as er:
            if er.args[0] == 5:  # IO error, device disappeared
                print("device disconnected")
                break

        if n > 0:
            dev_data_in = transport.serial.read(n)
            if dev_data_in is not None:
                if escape_non_printable:
                    # Pass data through to the console, with escaping of non-printables.
                    console_data_out = bytearray()
                    for c in dev_data_in:
                        if c in (8, 9, 10, 13, 27) or 32 <= c <= 126:
                            console_data_out.append(c)
                        else:
                            console_data_out.extend(b"[%02x]" % c)
                else:
                    console_data_out = dev_data_in
                console_out_write(console_data_out)
                for c in dev_data_in:
                    # Set at_prompt=True if we see the prompt string
                    # Stays set till the next newline char
                    if c == prompt[prompt_char_count]:
                        at_prompt = False  # Reset at_prompt after '\n'
                        prompt_char_count += 1
                        if prompt_char_count == len(prompt):
                            prompt_char_count = 0
                            at_prompt = True
                    else:
                        prompt_char_count = 0


# Override the mpremote main repl loop!!!
mpremote_repl.do_repl_main_loop = my_do_repl_main_loop


def main() -> int:
    # Set locale for file listings, etc.
    locale.setlocale(locale.LC_ALL, "")

    return mpremote.main.main()  # type: ignore


if __name__ == "__main__":
    main()
