# basecommands.py: Command line processor for mpremote-style filesystem commands
# at the micropython prompt using an Ipython-like '%' escape sequence.
#
# MIT License Copyright (c) 2021 @glenn20

# For python<3.10: Allow method type annotations to reference enclosing class
# Allow type1 | type2 instead of Union[type1, type2]
# Allow list[str] instead of List[str]
from __future__ import annotations

import cmd
import fnmatch
import glob
import inspect
import itertools
import json
import logging
import os
import re
import readline
import shlex
import shutil
import tempfile
import time
from pathlib import Path
from traceback import print_exc
from typing import Any, Iterable

from mpremote_path import Board
from mpremote_path import MPRemotePath as MPath

from . import pathfun
from .colour import AnsiColour

# Type alias for the list of command arguments
Argslist = list[str]

HISTORY_FILE = "~/.mpr-thing.history"
OPTIONS_FILE = ".mpr-thing.options"
RC_FILE = ".mpr-thing.rc"


# A context manager to catch exceptions from mpremote SerialTransport and others
class catcher:
    """Catch and report exceptions commonly raised by the mpr-thing tool.
    Eg.
        with catcher():
            id = board.eval("unique_id()")"""

    nested_depth = 0

    def __enter__(self):
        catcher.nested_depth += 1
        return self

    def __exit__(self, exc_type: Any, exc_value: Any, traceback: Any) -> bool:
        catcher.nested_depth -= 1
        if exc_type is KeyboardInterrupt:
            print("Keyboard Interrupt.")
            if catcher.nested_depth > 0:
                return False  # Propagate the exception to the top-level catcher
        if exc_type in (OSError, FileNotFoundError):
            print(f"{exc_type.__name__}: {exc_value}")
        elif exc_type is Exception:
            print("Error:: ", end="")
            print(f"{exc_type.__name__}: {exc_value}")
            print(traceback)
        return True


# Support for the interactive command line interpreter for running shell-like
# commands on the remote board. This base class contains all the initialisation
# and utility methods as well as some necessary overrides for the cmd.Cmd class.
class BaseCommands(cmd.Cmd):
    base_prompt: str = "\r>>> "
    doc_leader: str = "================================================================"
    doc_header: str = (
        'Execute "%magic" commands at the micropython prompt, eg: %ls /\n'
        'Use "%%" to enter multiple command mode.\n'
        "Further help is available for the following commands:\n" + doc_leader
    )
    ruler = ""  # Cmd.ruler is broken if doc_header is multi-line
    # Commands that complete filenames on the board
    remote_cmds = (
        "fs",
        "ls",
        "cat",
        "edit",
        "touch",
        "mv",
        "cp",
        "rm",
        "get",
        "cd",
        "mkdir",
        "rmdir",
        "echo",
    )
    # Commands that complete on directory names
    dir_cmds = ("cd", "mkdir", "rmdir", "mount", "lcd")
    # Commands that have no completion
    noglob_cmds = ("eval", "exec", "alias", "unalias", "set")

    def __init__(self, board: Board):
        self.initialised = False
        self.colour = AnsiColour()
        self.multi_cmd_mode = False
        self.shell_mode = False
        self.board = board
        self.prompt = self.base_prompt
        self.prompt_fmt = (
            "{bold-cyan}{id} {yellow}{platform} ({free}){bold-blue}{pwd}> "
        )
        self.prompt_colour = "cyan"  # Colour of the short prompt
        self.shell_colour = "magenta"  # Colour of the short prompt
        self.command_colour = "reset"  # Colour of the commandline
        self.output_colour = "reset"  # Colour of the command output
        self.alias: dict[str, str] = {}  # Command aliases
        self.params: dict[str, Any] = {}  # Params we can use in prompt
        self.names: dict[str, str] = {}  # Map device unique_ids to names
        self.lsspec: dict[str, str] = {}  # Extra colour specs for %ls
        readline.set_completer_delims(" \t\n>;")

        # Cmd.cmdloop() overrides completion settings in ~/.inputrc
        # We can disable this by setting completekey=''
        super().__init__(completekey="")
        # But then we need to load the completer function ourselves
        self.old_completer = readline.get_completer()
        readline.set_completer(self.complete)  # type: ignore

        # Load the readline history file
        self.history_file = os.path.expanduser(HISTORY_FILE)
        if os.path.isfile(self.history_file):
            readline.read_history_file(self.history_file)
        MPath.connect(self.board)
        self.logging = str(
            logging.getLevelName(logging.getLogger().getEffectiveLevel())
        ).lower()

    def load_command_file(self, file: str) -> bool:
        'Read commands from "file" first in home folder then local folder.'
        for rcfile in [os.path.expanduser("~/" + file), file]:
            lines = []
            try:
                # Load and close file before processing as cmds may force
                # re-write of file (eg. ~/.mpr-thing.options)
                with open(rcfile, "r", encoding="utf-8") as f:
                    lines = list(f)
            except OSError:
                pass
            for i, line in enumerate(lines):
                try:
                    self.onecmd(line)
                except Exception as err:  # pylint: disable=broad-except
                    print(f"Error loading {rcfile} on line {i + 1}: {line.strip()}")
                    print(f"  {type(err).__name__}: {err}")
            return True
        return False

    def initialise(self) -> None:
        if self.initialised:
            return
        self.initialised = True
        # Load/reload the helper code onto the micropython board.
        self.load_command_file(OPTIONS_FILE)
        self.load_command_file(RC_FILE)

    def reset(self) -> None:
        pass

    def load_board_params(self) -> None:
        "Initialise the board parameters - used in the longform prompt"
        if "id" in self.params:
            return
        # Load these parameters only once for each board
        device_name = self.board.device_name()
        self.params["device"] = device_name
        self.params["dev"] = re.sub(  # /dev/ttyUSB1 -> u1
            r"^/dev/tty(.).*(.)$",
            r"\1\2",
            re.sub(r"^COM([0-9]+)$", r"c\1", device_name.lower()),  # COM2 -> c2
        )
        with catcher():
            self.board.exec("import os, sys, gc, time; from machine import unique_id")
            self.params["platform"] = self.board.eval("sys.platform")
        with catcher():
            self.params["unique_id"] = self.board.eval('unique_id().hex(":")')
        with catcher():
            self.params.update(self.board.eval('eval(f"dict{os.uname()}")'))
        unique_id = self.params.get("unique_id", "")
        self.params["id"] = unique_id[-8:]  # Last 3 octets
        # Add the ansi colour names
        self.params.update({c: self.colour.ansi(c) for c in self.colour.colour})

    def set_prompt(self) -> None:
        "Set the prompt using the prompt_fmt string."
        if not self.multi_cmd_mode:
            self.prompt = self.colour(self.prompt_colour, self.base_prompt) + (
                (self.colour.ansi(self.command_colour) + "%")
                if not self.shell_mode
                else (self.colour.ansi(self.shell_colour) + "!")
            )
            return
        self.load_board_params()
        pwd, alloc, free = self.board.eval(
            "(os.getcwd(), gc.mem_alloc(), gc.mem_free())"
        )

        free_pc = round(100 * free / (alloc + free), None) if alloc > 0 else 0
        free_delta = max(0, self.params.get("free", free) - free)

        # Update some dynamic info for the prompt
        self.params["pwd"] = pwd
        self.params["free_delta"] = free_delta
        self.params["free"] = free
        self.params["free_pc"] = free_pc
        self.params["lcd"] = os.getcwd()
        self.params["lcd3"] = "/".join(os.getcwd().rsplit("/", 3)[1:])
        self.params["lcd2"] = "/".join(os.getcwd().rsplit("/", 2)[1:])
        self.params["lcd1"] = "/".join(os.getcwd().rsplit("/", 1)[1:])
        self.params["name"] = self.names.get(  # Look up name for board
            self.params["unique_id"], self.params["id"]
        )
        prompt_colours = {
            "free": ("green" if free_pc > 50 else "yellow" if free_pc > 25 else "red"),
            "free_pc": (
                "green" if free_pc > 50 else "yellow" if free_pc > 25 else "red"
            ),
        }
        prompt_map = {
            k: self.colour(prompt_colours.get(k, ""), v) for k, v in self.params.items()
        }

        self.prompt = (
            # Make GNU readline calculate the length of the colour prompt
            # correctly. See readline.rl_expand_prompt() docs.
            re.sub(
                "(\x1b\\[[0-9;]+m)",
                "\x01\\1\x02",
                # Make colour reset act like a colour stack
                self.colour.colour_stack(
                    # Build the prompt from prompt_fmt (set with %set cmd)
                    self.prompt_fmt.format_map(prompt_map)
                ),
            )
            + self.colour.ansi(self.command_colour)
        )

    def print_files(self, files: Iterable[Path], opts: str) -> None:
        """Print a file listing (long or short style) from data returned
        from the board."""
        # Pretty printing for files on the board
        files = list(files)
        if not files:
            return
        columns = shutil.get_terminal_size().columns
        if "l" in opts:
            # Long listing style - data is a list of filenames
            for f in files:
                st = f.stat()
                size = st.st_size if not f.is_dir() else 0
                t = time.strftime("%c", time.localtime(st.st_mtime)).replace(" 0", "  ")
                filename = self.colour.pathname(f)
                print(f"{size:9d} {t[:-3]} {filename}")
        else:
            # Short listing style - data is a list of filenames
            if len(files) < 20 and sum(len(f.name) + 2 for f in files) < columns:
                # Print all on one line
                for f in files:
                    print(self.colour.pathname(f), end="  ")
                print("")
            else:
                # Print in columns - by row
                w = max(len(f.name) for f in files) + 2
                spaces = " " * w
                cols = columns // w
                for i, f in enumerate(files):
                    n = i + 1
                    print(
                        self.colour.pathname(f),
                        spaces[len(f.name) :],
                        sep="",
                        end=("" if n % cols and n < len(files) else "\n"),
                    )

    def do_include(self, args: Argslist) -> None:
        for arg in args:
            self.load_command_file(arg)

    def do_shell(self, args: Argslist) -> None:
        """
        Execute shell commands from the "%" prompt as well, eg:
            %!date"""
        if args and len(args) == 2 and args[0] == "cd":
            os.chdir(args[1])
            return
        with tempfile.TemporaryDirectory() as tmpdir:
            names: list[tuple[MPath, Path]] = []
            new_args: list[str] = []
            for arg in args:
                if arg.startswith(":"):
                    src, dst = MPath(arg[1:]), Path(tmpdir)
                    dst = pathfun.copy_into_dir(src, dst)
                    if dst is None:
                        raise ValueError(f"Error copying {src!r} to {dst!r}")
                    names.append((src, dst))
                    new_args.append(str(dst))
                else:
                    new_args.append(arg)

            os.system(" ".join(new_args))
            # for src, dest in names:
            #     remote.board.put(str(dest), src)

    def do_alias(self, args: Argslist) -> None:
        """
        Assign an alias for other commands: eg:
            %alias ll="ls -l" lr="ls -lR"
            %alias connect='exec "network.WLAN(0).connect(\"{}\", \"{}\")"'
        You can use "{}" or "{2}" format specifiers to consume arguments when
        you use the alias: eg:
            %connect ssid password
        Any arguments which are not consumed by format specfiers will be
        added to the command after expanding the alias, eg:
            %ll /lib
        """
        if not args:
            for k, v in self.alias.items():
                print(f'alias "{k}"="{v}"')
            return

        for arg in args:
            alias, value = arg.split("=", maxsplit=1)
            if not alias or not value:
                print(f'Invalid alias: "{arg}"')
                continue
            self.alias[alias] = value

        # Now, save the aliases in the options file
        self.save_options()

    def do_unalias(self, args: Argslist) -> None:
        """
        Delete aliases which have been set with the % alias command:
            %unalias ll [...]"""
        for arg in args:
            del self.alias[arg]
        self.save_options()

    def do_set(self, args: Argslist) -> None:  # noqa: C901 too complex
        if not args:
            print(f'set prompt="{self.prompt_fmt}"')
            print(f'set promptcolour="{self.prompt_colour}"')
            print(f'set commandcolour="{self.command_colour}"')
            print(f'set shellcolour="{self.shell_colour}"')
            print(f'set outputcolour="{self.output_colour}"')
            print(f'set name="{self.names[self.params["unique_id"]]}"')
            print(f"set names='{json.dumps(self.names)}'")
            print(f"set lscolour='{json.dumps(self.lsspec)}'")
            print(f'set logging="{self.logging}"')
            return

        for arg in args:
            try:
                key, value = arg.split("=", maxsplit=1)
            except ValueError:
                key, value = "", ""
            if not key or not value:
                print("%set: invalid option setting:", arg)
                continue
            if key == "prompt":
                saved_fmt = self.prompt_fmt
                saved_multi_cmd_mode = self.multi_cmd_mode
                try:
                    self.prompt_fmt = value
                    self.multi_cmd_mode = True
                    self.set_prompt()  # Check for errors in the prompt
                except KeyError as err:  # Restore the old prompt_fmt
                    print("%set prompt: Invalid key in prompt:", err)
                    self.prompt_fmt = saved_fmt
                finally:
                    self.multi_cmd_mode = saved_multi_cmd_mode
                    self.set_prompt()  # Restore the right prompt
            elif key in ["promptcolour", "promptcolor"]:
                ansi = self.colour.ansi(value)
                if ansi[0] == "\x1b":
                    self.prompt_colour = value
                else:
                    print("%set: invalid colour:", value)
            elif key in ["commandcolour", "commandcolor"]:
                ansi = self.colour.ansi(value)
                if ansi[0] == "\x1b":
                    self.command_colour = value
                else:
                    print("%set: invalid colour:", value)
            elif key in ["shellcolour", "shellcolor"]:
                ansi = self.colour.ansi(value)
                if ansi[0] == "\x1b":
                    self.shell_colour = value
                else:
                    print("%set: invalid colour:", value)
            elif key in ["outputcolour", "outputcolor"]:
                ansi = self.colour.ansi(value)
                if ansi[0] == "\x1b":
                    self.output_colour = value
                else:
                    print("%set: invalid colour:", value)
            elif key == "names":
                try:
                    self.names.update(json.loads(value))
                except ValueError as err:
                    print("%set:", err)
            elif key == "name":
                self.load_board_params()
                self.names[self.params["unique_id"]] = value
            elif key in ["lscolour", "lscolor"]:
                d: dict[str, str] = {}
                d.update(json.loads(value))
                for k, v in d.items():
                    colour = self.colour.ansi(v)
                    if colour[0] != "\x1b":
                        print("%set: unknown colour:", v)
                        continue
                    self.lsspec[k.lstrip("*")] = v
                self.colour.spec.update(self.lsspec)
            elif key == "logging":
                for arg in value.split(","):
                    pair = arg.split("=", maxsplit=1)
                    name, level = pair if len(pair) == 2 else (None, pair[0])
                    logging.getLogger(name).setLevel(level.upper())
                self.logging = value  # type: ignore
            else:
                print("%set: unknown key:", key)
        self.save_options()

    def save_options(self) -> None:
        "Save the options in a startup file."
        if not self.initialised:
            return  # Don't save if we are reading from the options file
        with open(
            OPTIONS_FILE
            if os.path.isfile(OPTIONS_FILE)
            else os.path.expanduser("~/" + OPTIONS_FILE),
            "w",
            encoding="utf-8",
        ) as f:
            f.write("# Edit with caution: will be overwritten by mpr-thing.\n")
            f.write(f'set prompt="{self.prompt_fmt}"\n')
            f.write(f'set promptcolour="{self.prompt_colour}"\n')
            f.write(f'set commandcolour="{self.command_colour}"\n')
            f.write(f'set shellcolour="{self.shell_colour}"\n')
            f.write(f'set outputcolour="{self.output_colour}"\n')
            f.write(f"set names='{json.dumps(self.names)}'\n")
            f.write(f"set lscolour='{json.dumps(self.lsspec)}'\n")
            for name, value in self.alias.items():
                f.write(f'alias "{name}"="{value}"\n')

    def help_set(self) -> None:
        print(
            inspect.cleandoc(
                """
        Set some options, eg:
            %set prompt='{cyan}{name}@{dev}-{sysname}-({free}){blue}{pwd}> '
            %set prompt='{cyan}{name}@{dev}({free}){green}{lcd1}:{blue}{pwd}> '
            %set promptcolour=cyan
            %set commandcolour=yellow
            %set outputcolour=cyan
            %set promptcolor=cyan
        Set and save the name of the current board (for use in prompt):
            %set name=node05
        Update the mapping of all device unique_ids and names (as json string):
            %set names='{"ab:cd:ef:01:23:45": "node01", ...}'
        Add extra colour specs (as json) for file listing with "ls":
            %set lscolour='{"di": "bold-blue", "*.py": "bold-cyan"}'
            %set lscolor='{"*.pyc": "magenta"}'
        Enable logging for a module or all modules:
            %set logging=warning
            %set logging=debug
            %set logging=warning,mpremote_path=debug
        """
            )
        )
        print(
            f"\nThese options will be automatically saved in ~/{OPTIONS_FILE}\n"
            f"or ./{OPTIONS_FILE} (if it exists).\n"
            f"\nPrompts are python format strings and may include:\n    ",
            end="",
        )
        self.load_board_params()
        for i, k in enumerate(k for k in self.params if not k.startswith("ansi")):
            print(f"{'{' + k + '}':15}", end="" if (i + 1) % 5 else "\n    ")
        print("and the ansi256 color codes: {ansi0}, {ansi1}, ...{ansi255}")

        print("\n")
        print(
            inspect.cleandoc(
                """
        Where:
            {device/dev}: full or short name for the serial device
            {sysname/nodename/release/version/machine}: set from os.uname()
            {unique_id/id} from machine.unique_id() (id is last 3 octets)
            {colour/bold-colour}: insert an ANSI colour sequence
            {reset}: pop the colour stack
            {bold/normal/underline/reverse}: insert an ANSI text sequence
            {pwd}: current working directory on board
            {free/_pc}: the current free heap memory in bytes/percentage
            {lcdn}: last n parts of local working directory
            {name}: name of current board or {id} if name is not set

        Completion of parameter names is supported by hitting the TAB key."""
            )
        )

    def default(self, line: str) -> bool:  # type: ignore
        "Process any commandlines not matching builtin commands."
        if not self.multi_cmd_mode and line.strip() == "%":
            # User typed '%%': Enter command line mode
            print(
                'Enter magic commands (try "help" for a list)\n'
                'Type "quit" or ctrl-D to return to micropython repl:'
            )
            self.multi_cmd_mode = True
            return not self.multi_cmd_mode
        elif self.multi_cmd_mode and line.strip() in ("exit", "quit", "EOF"):
            # End command line mode - typed "quit", "exit" or ctrl-D
            self.prompt = self.base_prompt
            self.multi_cmd_mode = False
            if line.strip() == "EOF":
                print()
            return not self.multi_cmd_mode

        if line.strip().startswith("#"):
            return not self.multi_cmd_mode  # Ignore comments

        print(f'Unknown command: "{line.strip()}"')
        return not self.multi_cmd_mode

    def do_help(self, args: Argslist) -> None:  # pylint: disable=arguments-renamed
        'List available commands with "help" or detailed help with "help cmd".'
        # Need to override Cmd.do_help since we abuse the args parameter
        if not args:
            super().do_help("")
            return
        arg = args[0]
        try:
            func = getattr(self, "help_" + arg)
        except AttributeError:
            try:
                doc = getattr(self, "do_" + arg).__doc__
                if doc:
                    self.stdout.write(inspect.cleandoc(doc))
                    self.stdout.write("\n")
                    return
            except AttributeError:
                pass
            self.stdout.write(f"{str(self.nohelp % (arg,))}\n")
            return
        func()

    def complete_local(self, word: str) -> Argslist:
        "Commandline completion for filenames on the local host."
        # Complete names starting with ":" as remote files.
        if word.startswith(":"):
            return [f":{f}" for f in self.complete_remote(word)]
        # Filename completion on local host ...this is a local shop for local people.
        sep = word.rfind("/")
        pre, post = word[: sep + 1], word[sep + 1 :]
        files = Path(pre or ".").expanduser().glob(post + "*")
        return sorted([pathfun.slashify(f) for f in files])

    def complete_remote(self, word: str) -> Argslist:
        # Complete names starting with ":" as local files.
        if word.startswith(":"):
            return [f":{f}" for f in self.complete_local(word.lstrip(":"))]
        # Execute filename completion on the board.
        sep = word.rfind("/")
        pre, post = word[: sep + 1], word[sep + 1 :]
        lsdir = (str(f) for f in MPath(pre or ".").iterdir())
        return [pre + f for f in lsdir if f.startswith(post)]

    def complete_params(self, word: str) -> Argslist:
        # Complete on board params, eg: set prompt="{de[TAB]
        sep = word.rfind("{")
        pre, post = word[: sep + 1], word[sep + 1 :]
        return [pre + k for k in self.params if k.startswith(post)] if sep >= 0 else []

    # Command line parsing, splitting and globbing
    def completedefault(self, *args: str) -> Argslist:  # type: ignore
        'Perform filename completion on "word".'
        word, line, *_ = args
        command = line.split()[0].lstrip("%")
        if self.shell_mode:
            command = "shell"
        # pre is the directory portion, post is the incomplete filename
        if command in ("set", "echo"):
            # Complete on board params, eg: set prompt="{de[TAB]
            return self.complete_params(word)
        elif command in self.noglob_cmds:
            # No filename completion for this command
            return []
        elif command in self.remote_cmds:
            # Execute filename completion on the board.
            files = self.complete_remote(word)
        else:
            files = self.complete_local(word)

        # Return all filenames or only directories if requested
        return (
            [f for f in files if f.endswith("/")] if command in self.dir_cmds else files
        )

    def glob_remote(self, word: str) -> Iterable[str]:
        'Expand glob patterns in the filename part of "path".'
        if "*" not in word and "?" not in word:
            return []
        sep = word.rfind("/")
        dir1, word = word[: sep + 1] or ".", word[sep + 1 :]
        files = (str(f) for f in MPath(dir1).iterdir())
        return (  # Just return the generator
            ("" if dir1 == "." else dir1) + str(f)
            for f in files
            if str(f)[0] != "." and fnmatch.fnmatch(str(f), word)
        )

    def glob_local(self, word: str) -> Iterable[str]:
        'Expand glob patterns in the filename part of "path".'
        return glob.iglob(os.path.expanduser(word))

    def expand_globs(self, args: Argslist) -> Iterable[str]:
        if args[0] in self.noglob_cmds:
            yield from args
            return
        globber = self.glob_remote if args[0] in self.remote_cmds else self.glob_local
        yield args[0]
        for arg in args[1:]:
            if arg:
                for f in list(globber(arg)) or [arg]:
                    yield f

    def split(self, line: str) -> Argslist:
        "Split the command line into tokens."
        # punctuation_chars=True ensures semicolons can split commands
        lex = shlex.shlex(line, None, True, True)
        lex.wordchars += ":"
        return list(lex)

    def expand_aliases(self, args: Argslist) -> Argslist:
        if not args or args[0] not in self.alias:
            return args

        alias = self.alias[args.pop(0)]

        # Set of arg indices to be consumed by fmt specifiers: {}, {:23}, ...
        used = set(range(len(re.findall(r"{(:[^}]+)?}", alias))))
        # Add args consumed by {3}, {6:>23}, ...
        used.update(int(n) for n in re.findall(r"{([0-9]+):?[^}]*}", alias))

        # Expand the alias: can include format specifiers: {}, {3}, ...
        new_args = self.split(alias.format(*args))
        # Add any unused args to the end of the line
        new_args.extend(arg for n, arg in enumerate(args) if n not in used)

        return new_args

    def process_args(self, args: Argslist) -> Argslist:
        "Expand aliases, macros and glob expansions."

        # Get the first command if multiple commands on one line.
        # Push the rest of the args back onto the command queue
        # eg: ls /lib ; rm /main.py
        def split_semicolons(args: Argslist) -> Argslist:
            # Split argslist by semicolons
            if not args or ";" not in args:
                return args
            argslist = (  # [[arg1, ...], [arg1,...], ...]
                list(words)
                for key, words in itertools.groupby(args, lambda arg: arg == ";")
                if not key
            )
            # Get args for the first subcommand
            args = next(argslist) if args[0] != ";" else []
            # Push the rest of the args onto the front of the cmd queue
            self.cmdqueue[0:0] = list(argslist)  # type: ignore
            return args

        args = split_semicolons(args)
        args = self.expand_aliases(args)
        args = split_semicolons(args)  # Alias may expand to include ';'
        args = list(self.expand_globs(args))  # Expand glob patterns

        return args

    # Override some control functions in the Cmd class
    # Ensure everything has been initialised.
    def preloop(self) -> None:
        self.set_prompt()

    def postloop(self) -> None:
        print(self.base_prompt, end="")  # Re-print the micropython prompt

    def onecmd(self, line: str) -> bool:
        """Override the default Cmd.onecmd()."""
        print(f"{self.colour.ansi('reset')}", end="", flush=True)
        if isinstance(line, list):
            # List of str is pushed back onto cmdqueue in self.split()
            args = line
        else:  # line is a string
            if self.multi_cmd_mode:  # Discard leading '%' in multi-cmd mode
                if line and line.startswith("%") and not line.startswith("%%"):
                    line = line[1:]
                    readline.replace_history_item(1, line)
            readline.write_history_file(self.history_file)

            # A command line read from the input
            if self.shell_mode:
                line = "shell " + line
            command, _, line = self.parseline(line)
            if not line:
                return self.emptyline()
            if not command:
                return self.default(line)

            # Split the command line into a list of args
            args = list(self.split(line))

        args = self.process_args(args)  # Expand aliases, macros and globs
        command, *args = args or [""]
        self.lastcmd = ""
        func = getattr(self, "do_" + command, None)
        if func:
            ret: bool = func(args)
        else:
            ret = self.default(" ".join([command, *args]))
        return ret

    def postcmd(self, stop: Any, line: str) -> bool:
        self.set_prompt()  # Setup our complicated prompt
        # Exit if we are in single command mode and no commands in the queue
        return not self.multi_cmd_mode and not self.cmdqueue

    def run(self, prefix: bytes = b"%") -> None:
        "Catch exceptions and restart the Cmd.cmdloop()."
        self.initialise()  # Load the helper code onto the board
        self.shell_mode = prefix == b"!"
        stop = False
        while not stop:
            try:
                self.cmdloop()
            except KeyboardInterrupt:
                print()
            except Exception as err:  # pylint: disable=broad-except
                print(f"Error in command: {err}\n{err.args}")
                print_exc()
                # raise
            finally:
                if stop := not self.multi_cmd_mode:
                    print(f"{self.colour.ansi('reset')}", end="")
                    print(self.base_prompt, end="", flush=True)
        self.shell_mode = False

    def emptyline(self) -> bool:  # Else empty lines repeat last command
        return not self.multi_cmd_mode
