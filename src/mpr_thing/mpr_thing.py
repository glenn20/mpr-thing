#!/usr/bin/env python3

# MIT License: Copyright (c) 2021 @glenn20

from __future__ import annotations

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

LOGGING_CONFIG_FILE = "logging.yaml"


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


def query_console(
    console_in: ConsolePosix | ConsoleWindows,
    writer: Writer,
    query: str,
    response_pattern: str,
) -> re.Match | None:
    "Send an escape query to the console and wait for a response."
    writer(query.encode())  # Send query to terminal
    response = b""
    for _ in range(50):  # Don't wait forever - just in case
        if isinstance(console_in, ConsolePosix):
            select.select([console_in.infd], [], [], 0.1)
        else:
            # TODO: Windows terminal code is untested - I hope it works...
            for _ in range(10):  # Don't wait forever - just in case
                if console_in.inWaiting():
                    break
                time.sleep(0.01)
        c = console_in.readchar()
        if c:
            response += c
            if match := re.match(response_pattern, response.decode()):
                return match
    else:
        return None


def cursor_column(console_in: ConsolePosix | ConsoleWindows, writer: Writer) -> int:
    "Query the console to get the current cursor column number."
    match = query_console(console_in, writer, "\x1b[6n", r"^\x1b\[(\d+);(\d+)R")
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

    seen_prompt, beginning_of_line = False, False
    prompt = b"\n>>> "
    serial_buffer = b""  # Keep track of last chars from serial port
    transport: SerialTransport = state.transport  # type: ignore
    magic_command_processor = RemoteCmd(Board(transport, console_out_write))

    while True:
        console_in.waitchar(transport.serial)
        c = console_in.readchar()
        if c:
            if c in (b"\x1d", b"\x18"):  # ctrl-] or ctrl-x, quit
                break
            elif c == b"\x04":  # ctrl-D
                # do a soft reset and reload the filesystem hook
                transport.write_ctrl_d(console_out_write)
                magic_command_processor.initialised = False
                beginning_of_line = True
            elif c == b"\x12":  # ctrl-R
                # Toggle DTR (hard reset) and reload the filesystem hook
                hard_reset(transport)
                magic_command_processor.initialised = False
                beginning_of_line = True
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
                beginning_of_line = True
                transport.exit_raw_repl()
            elif (
                c in b"%!"
                and seen_prompt  # Magic sequence if at start of line
                and (
                    beginning_of_line
                    or cursor_column(console_in, console_out_write) == len(prompt)
                )
            ):
                console_out_write(b"\r\x1b[2K")  # Clear line before rewriting prompt
                try:
                    console_in.exit()
                    magic_command_processor.run(c)
                finally:
                    console_in.enter()
                beginning_of_line = True
            else:
                transport.serial.write(c)
                beginning_of_line = c in b"\r\n"  # Set beginning of line if CR or LF

        n = 0
        try:
            n = transport.serial.inWaiting()  # type: ignore
        except OSError as er:
            if er.args[0] == 5:  # IO error, device disappeared
                print("device disconnected")
                break

        if n > 0:
            dev_data_in = transport.serial.read(n)
            if dev_data_in:
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
                # mpr_thing: Set seen_prompt=True if we see the expected prompt
                # string coming from micropython on the serial port.
                if b"\n" in dev_data_in:
                    seen_prompt = False
                serial_buffer = serial_buffer + dev_data_in
                if not seen_prompt:
                    seen_prompt = prompt in serial_buffer
                serial_buffer = serial_buffer[-len(prompt) :]


# Override the mpremote main repl loop!!!
mpremote_repl.do_repl_main_loop = my_do_repl_main_loop


def main() -> int:
    import logging

    locale.setlocale(locale.LC_ALL, "")  # Set locale for file listings, etc.
    logging.basicConfig(format="%(levelname)s %(message)s", level=logging.WARNING)

    return mpremote.main.main()  # type: ignore


if __name__ == "__main__":
    main()
