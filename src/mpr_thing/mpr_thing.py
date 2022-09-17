# MIT License: Copyright (c) 2021 @glenn20

# vscode-fold=1


import select, re, sys, time, locale
from typing import Callable
from serial import Serial

from mpremote import main as mpremote_main
from mpremote.pyboard import PyboardError
from mpremote.pyboardextended import PyboardExtended
from mpremote.console import ConsolePosix, ConsoleWindows

from .board import Board
from .local_commands import LocalCmd
from .remote_commands import RemoteCmd

Writer = Callable[[bytes], None]  # A type alias for console write functions


def hard_reset(pyb: PyboardExtended) -> None:
    'Toggle DTR on the serial port to force a hardware reset of the board.'
    while hasattr(pyb.serial, 'orig_serial'):
        pyb.serial = pyb.serial.orig_serial
    if isinstance(pyb.serial, Serial):
        serial = pyb.serial
        if hasattr(serial, 'dtr'):
            serial.dtr = not serial.dtr
            time.sleep(0.1)
            serial.dtr = not serial.dtr
        pyb.mounted = False


def cursor_column(
        console_in: ConsolePosix | ConsoleWindows,
        writer: Writer) -> int:
    'Query the console to get the current cursor column number.'
    writer(b'\x1b[6n')   # Query terminal for cursor position
    buf = b''
    for _ in range(10):             # Don't wait forever - just in case
        if isinstance(console_in, ConsolePosix):
            select.select([console_in.infd], [], [], 0.1)
        else:
            # TODO: Windows terminal code is untested - I hope it works...
            for i in range(10):     # Don't wait forever - just in case
                if console_in.inWaiting():
                    break
                time.sleep(0.01)
        c = console_in.readchar()
        if c is not None:
            buf += c
            if c == b'R':           # Wait for end of escape sequence
                break
    else:
        return -1

    match = re.match(r'^\x1b\[(\d)*;(\d*)R', buf.decode())
    return int(match.groups()[1]) if match else -1


# This is going to override do_repl_main_loop in the mpremote module
# It interprets a "!" or "%" character typed at the start of a line
# at the base python prompt as starting a "magic" command.
def my_do_repl_main_loop(   # noqa: C901 - ignore function is too complex
        pyb:                PyboardExtended,
        console_in:         ConsolePosix | ConsoleWindows,
        console_out_write:  Writer,
        *,
        code_to_inject:     bytes,
        file_to_inject:     str) -> None:
    'An overload function for the main repl loop in "mpremote".'

    at_prompt, beginning_of_line, prompt_char_count = False, False, 0
    local, remote = LocalCmd(), RemoteCmd(Board(pyb, console_out_write))
    prompt = b"\n>>> "

    while True:
        console_in.waitchar(pyb.serial)
        c = console_in.readchar()
        if c:
            if c == b"\x1d":  # ctrl-], quit
                break
            elif c == b"\x04":  # ctrl-D
                # do a soft reset and reload the filesystem hook
                pyb.write_ctrl_d(console_out_write)
                beginning_of_line = True
                remote.reset_hooks()
            elif c == b"\x12":  # ctrl-R
                # Toggle DTR (hard reset) and reload the filesystem hook
                hard_reset(pyb)
                beginning_of_line = True
                remote.reset_hooks()
            elif c == b"\x0a" and code_to_inject is not None:
                pyb.serial.write(code_to_inject)    # ctrl-j, inject code
            elif c == b"\x0b" and file_to_inject is not None:
                console_out_write(                  # ctrl-k, inject script
                    bytes("Injecting %s\r\n" % file_to_inject, "utf8"))
                pyb.enter_raw_repl(soft_reset=False)
                with open(file_to_inject, "rb") as f:
                    pyfile = f.read()
                try:
                    pyb.exec_raw_no_follow(pyfile)
                except PyboardError as er:
                    console_out_write(b"Error:\r\n")
                    console_out_write(repr(er).encode('utf-8'))
                pyb.exit_raw_repl()
                beginning_of_line = True
            elif (c == b"!" and at_prompt  # Magic sequence if at start of line
                    and (beginning_of_line or
                         cursor_column(
                             console_in, console_out_write) == len(prompt))):
                console_out_write(b"\x1b[2K")  # Clear other chars on line
                console_in.exit()
                try:
                    local.cmdloop()
                finally:
                    console_in.enter()
                pyb.serial.write(b"\x0d")  # Force another prompt
                beginning_of_line = True
            elif (c == b"%" and at_prompt  # Magic sequence if at start of line
                    and (beginning_of_line or
                         cursor_column(
                             console_in, console_out_write) == len(prompt))):
                console_out_write(b"\x1b[2K")  # Clear other chars on line
                console_in.exit()
                try:
                    remote.cmdloop()
                finally:
                    console_in.enter()
                beginning_of_line = True
            elif c == b"\x0d":      # ctrl-m: carriage return
                pyb.serial.write(c)
                beginning_of_line = True
            else:
                pyb.serial.write(c)
                beginning_of_line = False

        n = 0
        try:
            n = pyb.serial.inWaiting()
        except OSError as er:
            if er.args[0] == 5:  # IO error, device disappeared
                print("device disconnected")
                break

        if n > 0:
            c = pyb.serial.read(1)
            if c is not None:
                # pass character through to the console
                oc = ord(c)
                if oc in (8, 9, 10, 13, 27) or 32 <= oc <= 126:
                    console_out_write(c)
                else:
                    console_out_write(b"[%02x]" % ord(c))

                # Set at_prompt=True if we see the prompt string
                # Stays set till the next newline char
                if oc == prompt[prompt_char_count]:
                    at_prompt = False           # Reset at_prompt after '\n'
                    prompt_char_count += 1
                    if prompt_char_count == len(prompt):
                        prompt_char_count = 0
                        at_prompt = True
                else:
                    prompt_char_count = 0


# Override the mpremote main repl loop!!!
mpremote_main.do_repl_main_loop = my_do_repl_main_loop


def main() -> int:
    # Set locale for file listings, etc.
    locale.setlocale(locale.LC_ALL, '')

    return mpremote_main.main()     # type: ignore


if __name__ == "__main__":
    sys.exit(mpremote_main.main())
