# basecommands.py: Command line processor for mpremote-style filesystem commands
# at the micropython prompt using an Ipython-like '%' escape sequence.
#
# MIT License Copyright (c) 2021 @glenn20

# For python<3.10: Allow method type annotations to reference enclosing class
# Allow type1 | type2 instead of Union[type1, type2]
# Allow list[str] instead of List[str]
from __future__ import annotations

import cmd
import inspect
import json
import logging
import os
import re
import readline
import shlex
import subprocess
import tempfile
import time
from contextlib import suppress
from pathlib import Path
from traceback import print_exc
from typing import Any

from mpremote_path import MPRemotePath as MPath
from mpremote_path.util import mpfs

from .colour import AnsiColour
from .console import console

# Type alias for the list of command arguments
Argslist = list[str]

# Set up the default path for mpr-thing config files.
CONFIGPATH = Path(
    os.environ.get("APPDATA")  # Windows
    or os.environ.get("XDG_CONFIG_HOME")  # Linux/Mac
    or os.path.expanduser("~/.config"),
    "mpr-thing",
)
CONFIGPATH.mkdir(parents=True, exist_ok=True)

HISTORY_FILE = CONFIGPATH / "history"
OPTIONS_FILE = CONFIGPATH / "options"
RC_FILE = CONFIGPATH / "startup-commands"


def slashify(path: Path | str) -> str:
    """Return `path` as a string (with a trailing slash if it is a
    directory)."""
    if isinstance(path, Path) and path.is_dir():
        return f"{path}/"
    else:
        return str(path)


# Support for the interactive command line interpreter for running shell-like
# commands on the remote board. This base class contains all the initialisation
# and utility methods as well as some necessary overrides for the cmd.Cmd class.
class BaseCommands(cmd.Cmd):
    initialised: bool  # Whether the command line has been initialised
    multi_cmd_mode: bool  # Whether we are in multi-command mode
    shell_mode: bool  # Whether we are in shell mode (eg: ! command)
    prompt: str  # The current prompt string
    long_prompt: str  # The format string for the prompt
    prompt_colour: str  # Colour of the short prompt
    shell_colour: str  # Colour of the shell prompt
    command_colour: str  # Colour of the commandline
    output_colour: str  # Colour of the command output
    aliases: dict[str, str]  # Command aliases
    parameters: dict[str, Any]  # Params we can use in prompt
    device_names: dict[str, str]  # Map device unique_ids to names
    lsspec: dict[str, str]  # Extra colour specs for %ls
    cmd_time: int  # Time to execute command on board
    base_prompt: str = ">>> "
    doc_leader: str = "================================================================"
    doc_header: str = (
        'Execute "%mpremote"-like commands at the micropython prompt, eg: %ls /\n'
        'Use "%%" to enter multiple command mode.\n'
        "Further help is available for the following commands:\n" + doc_leader
    )
    ruler = ""  # Cmd.ruler is broken if doc_header is multi-line
    completion_type = {
        k: v.split(" ")
        for k, v in {
            "remote_files": "fs ls cat edit touch mv cp rm get cd mkdir rmdir echo",
            "directories": "cd mkdir rmdir mount lcd",
            "none": "eval exec alias unalias set",
            "params": "set echo",
        }.items()
    }

    def __init__(self) -> None:
        self.initialised = False
        self.colour = AnsiColour()
        self.multi_cmd_mode = False
        self.shell_mode = False
        self.prompt = self.base_prompt
        self.long_prompt = "> "
        self.prompt_colour = "cyan"  # Colour of the short prompt
        self.shell_colour = "magenta"  # Colour of the shell prompt
        self.command_colour = "reset"  # Colour of the commandline
        self.output_colour = "reset"  # Colour of the command output
        self.aliases = {}  # Command aliases
        self.parameters = {}  # Parameters we can use in the prompt
        self.device_names = {}  # Map device unique_ids to names
        self.lsspec = {}  # Extra colour specs for %ls
        self.cmd_time = 0  # Time to execute command on board

        # Readline setup and configuration
        # Cmd.cmdloop() binds the completion character to "tab" by default,
        # potentially overriding user preferences in ~/.inputrc.
        readline.set_completer_delims(" \t\n>;")
        super().__init__(completekey="")  # Prevent Cmd from setting completekey
        # So, we need to configure the completer ourselves.
        self.old_completer = readline.get_completer()
        readline.set_completer(self.complete)  # type: ignore
        if HISTORY_FILE.is_file():
            readline.read_history_file(str(HISTORY_FILE))

        # The readline module may use libedit instead of GNU readline,
        # so we need to bind the tab key to rl_complete for compatibility.
        if os.getenv("READLINE") == "libedit":
            readline.parse_and_bind("bind ^I rl_complete")  # libedit
        else:
            readline.parse_and_bind("tab: complete")  # GNU readline
        readline.parse_and_bind("set completion-ignore-case on")

        self.logging = str(
            logging.getLevelName(logging.getLogger().getEffectiveLevel())
        ).lower()
        self.parameters["time_ms"] = self.cmd_time
        # Add the ansi colour names
        self.parameters.update({c: self.colour.ansi(c) for c in self.colour.colour})
        self.load_command_file(OPTIONS_FILE)

    def initialise(self) -> bool:
        if self.initialised:
            return False
        self.load_command_file(RC_FILE)
        self.initialised = True
        return True

    def load_command_file(self, file: Path) -> bool:
        'Read commands from "file".'
        if not file.is_file():
            return False  # File does not exist
        # Load and close file before processing as cmds may force
        # re-write of file (eg. ~/.config/mpr-thing/options)
        with open(file, "r", encoding="utf-8") as f:
            lines = list(f)
        for i, line in enumerate(lines):
            try:
                self.onecmd(line)
            except Exception as err:  # pylint: disable=broad-except
                print(f"Error loading {file} on line {i + 1}: {line.strip()}")
                print(f"  {type(err).__name__}: {err}")
        return True

    def do_include(self, args: Argslist) -> None:
        for arg in args:
            self.load_command_file(Path(arg))

    def do_shell(self, args: Argslist) -> None:
        """
        Execute shell commands on the local host, eg:
            !date
        Filenames starting with `:` will be copied from the board to a temporary
        file on the host, eg:
            !grep secrets :main.py"""
        if args and len(args) == 2 and args[0] == "cd":
            os.chdir(args[1])
            return
        with tempfile.TemporaryDirectory() as tmpdir:
            # TODO: Prevent name clashes for files fromn different dirs
            new_args: list[str] = []
            for arg in args:
                if arg.startswith(":"):
                    mpfs.get(arg[1:], tmpdir)
                    arg = str(Path(tmpdir) / arg[1:])
                new_args.append(arg)

            with suppress(subprocess.CalledProcessError):
                if (shell := os.getenv("SHELL")) is None:
                    subprocess.run(new_args, shell=True, check=True)
                else:
                    # Use an interactive shell to run the command
                    subprocess.run([shell, "-ic", " ".join(new_args)], check=True)

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
            for k, v in self.aliases.items():
                print(f'alias "{k}"="{v}"')
            return

        for arg in args:
            alias, value = arg.split("=", maxsplit=1)
            if not alias or not value:
                print(f'Invalid alias: "{arg}"')
                continue
            self.aliases[alias] = value

        # Now, save the aliases in the options file
        if self.initialised:
            self.save_options()

    def do_unalias(self, args: Argslist) -> None:
        """
        Delete aliases which have been set with the % alias command:
            %unalias ll [...]"""
        for arg in args:
            del self.aliases[arg]
        if self.initialised:
            self.save_options()

    def do_set(self, args: Argslist) -> None:  # noqa: C901 too complex
        if not args:
            print(f'set prompt="{self.long_prompt}"')
            print(f'set promptcolour="{self.prompt_colour}"')
            print(f'set commandcolour="{self.command_colour}"')
            print(f'set shellcolour="{self.shell_colour}"')
            print(f'set outputcolour="{self.output_colour}"')
            print(f'set name="{self.device_names[self.parameters["unique_id"]]}"')
            print(f"set names='{json.dumps(self.device_names)}'")
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
                saved_fmt = self.long_prompt
                saved_multi_cmd_mode = self.multi_cmd_mode
                try:
                    self.long_prompt = value
                    self.multi_cmd_mode = self.initialised
                    self.set_prompt()  # Check for errors in the prompt
                except KeyError as err:  # Restore the old prompt_fmt
                    print(f"%set prompt={value!r}\r")
                    print(f"    Invalid key in prompt: {err}\r")
                    self.long_prompt = saved_fmt
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
                    self.device_names.update(json.loads(value))
                except ValueError as err:
                    print("%set:", err)
            elif key == "name":
                self.device_names[self.parameters["unique_id"]] = value
            elif key in ["lscolour", "lscolor"]:
                d: dict[str, str] = {}
                d.update(json.loads(value))
                for k, v in d.items():
                    colour = self.colour.ansi(v)
                    if colour[0] != "\x1b":
                        print("%set: unknown colour:", v)
                        continue
                    self.lsspec[k.lstrip("*")] = v
                self.colour.colour_spec.update(self.lsspec)
            elif key == "logging":
                for arg in value.split(","):
                    pair = arg.split("=", maxsplit=1)
                    name, level = pair if len(pair) == 2 else (None, pair[0])
                    logging.getLogger(name).setLevel(level.upper())
                self.logging = value
            else:
                print("%set: unknown key:", key)
        if self.initialised:
            self.save_options()

    def save_options(self) -> None:
        "Save the options in a startup file."
        with open(OPTIONS_FILE, "w", encoding="utf-8") as f:
            f.write("# mpr-thing options save file.\n")
            f.write("# Edit with caution: will be overwritten by mpr-thing.\n")
            f.write(f'set prompt="{self.long_prompt}"\n')
            f.write(f'set promptcolour="{self.prompt_colour}"\n')
            f.write(f'set commandcolour="{self.command_colour}"\n')
            f.write(f'set shellcolour="{self.shell_colour}"\n')
            f.write(f'set outputcolour="{self.output_colour}"\n')
            f.write(f"set names='{json.dumps(self.device_names)}'\n")
            f.write(f"set lscolour='{json.dumps(self.lsspec)}'\n")
            for name, value in self.aliases.items():
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
            f"\nThese options will be automatically saved in "
            f"~/.config/mpr-thing/{OPTIONS_FILE}\n"
            f"or ./{OPTIONS_FILE} (if it exists).\n"
            f"\nPrompts are python format strings and may include:\n    ",
            end="",
        )
        for i, k in enumerate(k for k in self.parameters if not k.startswith("ansi")):
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
            {free_delta}: the change in free heap memory during last command
            {time_ms}: time taken to execute last command in milliseconds
            {lcdn}: last n parts of local working directory
            {name}: name of current board or {id} if name is not set

        Completion of parameter names is supported by hitting the TAB key."""
            )
        )

    def default(self, line: str) -> bool:  # type: ignore
        "Process any commandlines not matching builtin commands."
        line = line.strip()
        if not self.multi_cmd_mode and line == "%":
            # User typed '%%': Enter command line mode
            print(
                'Enter magic commands (try "help" for a list)\n'
                'Type "quit" or ctrl-D to return to micropython repl:'
            )
            self.multi_cmd_mode = True
            self.set_prompt()
        elif self.multi_cmd_mode and line in ("exit", "quit", "EOF"):
            # End command line mode - typed "quit", "exit" or ctrl-D
            self.prompt = self.base_prompt
            self.multi_cmd_mode = False
            if line == "EOF":
                print()
        elif not line.startswith("#"):  # Ignore comments
            print(f'Unknown command: "{line}"')

        return not self.multi_cmd_mode

    def do_help(self, args: Argslist) -> None:  # type: ignore
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

    def complete_params(self, word: str) -> Argslist:
        """Complete on board params, eg: set prompt="{de[TAB]"""
        pre, sep, post = word.partition("{")
        return (
            [f"{pre}{sep}{k}}}" for k in self.parameters if k.startswith(post)]
            if sep
            else []
        )

    # Command line parsing, splitting and globbing
    def completedefault(self, *args: str) -> Argslist:  # type: ignore
        'Perform filename completion on "word".'
        word, line, *_ = args
        command = line.split()[0].lstrip("%")  # Ignore leading '%'
        if self.shell_mode:
            command = "shell"
        if command in self.completion_type["params"]:  # eg: set prompt="{de[TAB]
            return self.complete_params(word)
        elif command in self.completion_type["none"]:
            return []
        # A : prefix in word means to toggle whether to complete on board or host
        prefix, word = (":", word[1:]) if word.startswith(":") else ("", word)
        # Choose whether to complete filenames on the board or the host
        is_remote = command in self.completion_type["remote_files"]
        is_remote ^= bool(prefix)  # Toggle remote filename completion if : prefix
        pwd = MPath(".") if is_remote else Path(".")
        files = pwd.glob(word + "*")  # Get filenames that match
        if command in self.completion_type["directories"]:
            files = (p for p in files if p.is_dir())  # Only complete on directories
        # Put the : prefix back if present and add / to end of directories
        return [prefix + slashify(p) for p in files]

    def split_commandline(self, line: str) -> Argslist:
        "Split the command line into tokens."
        # punctuation_chars=";" ensures semicolons can split commands
        lex = shlex.shlex(line, infile=None, posix=True, punctuation_chars=";")
        lex.wordchars += ":"  # Treat colons as part of words
        return list(lex)

    def expand_aliases(self, args: Argslist) -> Argslist:
        "Expand command aliases, returning the new args list after expansion."
        if not args or args[0] not in self.aliases:
            return args

        alias = self.aliases[args.pop(0)]  # Get the value of the expanded alias

        # The expanded alias may include format specifiers: {}, {:23}, ... We
        # need to know which of the original args will be consumed by the alias,
        # `used` is a set containing the index of each original arg consumed by
        # the alias eg: alias ll="ls -l {} {3} {2:>23}"
        used = set(range(len(re.findall(r"{(:[^}]+)?}", alias))))
        # Add the arguments specified by index: {3}, {6:>23}, ...
        used.update(int(n) for n in re.findall(r"{([0-9]+):?[^}]*}", alias))

        # Expand the alias and split the result into a list of args
        # can include format specifiers: {}, {3}, ...
        new_args = self.split_commandline(alias.format(*args))
        # We then add any unused original args to the end of the line
        new_args.extend(arg for n, arg in enumerate(args) if n not in used)

        return new_args

    def process_args(self, args: Argslist) -> Argslist:
        "Expand aliases, macros and glob expansions."

        # Return the first command if multiple commands on one line.
        # Push the rest of the args back onto the command queue
        # eg: ls /lib ; rm /main.py
        def split_semicolons(args: Argslist) -> Argslist:
            # Split argslist by semicolons
            if not args or ";" not in args:
                return args
            argslist: list[Argslist] = [[]]
            for arg in args:
                if arg == ";":
                    argslist.append([])  # Start a new command at end of list
                else:
                    argslist[-1].append(arg)  # Add arg to the current command
            # args is the first subcommand, push the rest back onto the cmd queue
            args, self.cmdqueue[0:0] = argslist[0], argslist[1:]  # type: ignore
            return args

        args = split_semicolons(args)
        args = self.expand_aliases(args)
        args = split_semicolons(args)  # Alias may expand to include ';'

        return args

    # @override
    def onecmd(self, line: str) -> bool:
        """Override the default Cmd.onecmd()."""
        # print(f"{self.colour.ansi('reset')}", end="", flush=True)
        start_time = time.perf_counter()
        if isinstance(line, list):
            # List of str is pushed back onto cmdqueue in self.split_commandline()
            args = line
        else:  # line is a string
            if self.multi_cmd_mode:  # Discard leading '%' in multi-cmd mode
                if line and line.startswith("%") and not line.startswith("%%"):
                    line = line[1:]
                    readline.replace_history_item(1, line)
            readline.write_history_file(HISTORY_FILE)

            # A command line read from the input
            if self.shell_mode:
                line = "shell " + line
            command, _, line = self.parseline(line)
            if not line:
                return self.emptyline()
            if not command:
                return self.default(line)

            # Split the command line into a list of args
            args = self.split_commandline(line)

        args = self.process_args(args)  # Expand aliases, macros and globs
        command, *args = args or [""]
        self.lastcmd = ""
        func = getattr(self, "do_" + command, None)
        if func:
            ret: bool = func(args)
        else:
            ret = self.default(" ".join([command, *args]))
        self.cmd_time = round((time.perf_counter() - start_time) * 1000)
        return ret

    # @override
    def precmd(self, line: str) -> str:
        self.initialise()
        return line

    def _readline_escape_prompt(self, prompt: str) -> str:
        """Escape the colourised prompt for readline so it can calculate the
        length of the prompt correctly."""
        # See readline.rl_expand_prompt() docs.
        return re.sub("(\x1b\\[[0-9;]+m)", "\x01\\1\x02", prompt)

    def set_prompt(self) -> None:
        "Set the prompt using the prompt_fmt string."
        prompt = f"[{self.command_colour}]"
        prompt += (
            self.long_prompt.format_map(self.parameters) if self.multi_cmd_mode else
            self.base_prompt
        )  # fmt: off
        prompt += (
            f"[{self.command_colour}]" if self.multi_cmd_mode else
            f"[{self.shell_colour}]" if self.shell_mode else
            f"[{self.command_colour}]%"
        )  # fmt: off
        with console.capture() as capture:
            console.print(prompt, end="")
        self.prompt = self._readline_escape_prompt(capture.get())
        # self.prompt = self._readline_escape_prompt(
        #     self.colour.ansi(self.command_colour) +
        #     self.colour.colour_stack(prompt)
        #     + (
        #         self.colour.ansi(self.command_colour) if self.multi_cmd_mode else
        #         (self.colour.ansi(self.shell_colour) + "!") if self.shell_mode else
        #         (self.colour.ansi(self.command_colour) + "%")
        #     )
        # )  # fmt: off

    # @override
    def postcmd(self, stop: Any, line: str) -> bool:
        if self.multi_cmd_mode:
            self.set_prompt()  # Set the prompt for the next command
        # Exit if we are in single command mode and no commands in the queue
        return not self.multi_cmd_mode and not self.cmdqueue

    # @override
    def run(self, prefix: bytes = b"%") -> None:
        "Catch exceptions and restart the Cmd.cmdloop()."
        self.shell_mode = prefix == b"!"
        while True:
            try:
                self.set_prompt()
                self.cmdloop()
            except KeyboardInterrupt:
                print("^C")
            except Exception as err:  # pylint: disable=broad-except
                print(f"Error in command: {err}\n{err.args}")
                print_exc()
            finally:
                if not self.multi_cmd_mode:
                    # print(f"{self.colour.ansi('reset')}", end="")
                    print(self.base_prompt, end="", flush=True)
            if not self.multi_cmd_mode:
                break
        self.shell_mode = False

    # @override
    def emptyline(self) -> bool:  # Else empty lines repeat last command
        self.cmd_time = 0
        return not self.multi_cmd_mode
