# commands.py: Support for mpremote-style filesystem commands at the
# micropython prompt using an Ipython-like '%' escape sequence.
#
# MIT License
# Copyright (c) 2021 @glenn20

# For python<3.10: Allow method type annotations to reference enclosing class
# Allow type1 | type2 instead of Union[type1, type2]
# Allow list[str] instead of List[str]
from __future__ import annotations

import os, re, readline, time, cmd, shutil
import json, inspect, shlex, glob, fnmatch, itertools
from typing import Any, Iterable, Optional

from .catcher import catcher
from .remote_path import RemotePath
from .board import Board
from .colour import AnsiColour

# Type alias for the list of command arguments
Argslist = list[str]

HISTORY_FILE = '~/.mpr-thing.history'
OPTIONS_FILE = '.mpr-thing.options'
RC_FILE      = '.mpr-thing.rc'


# Support for the interactive command line interpreter for running shell-like
# commands on the remote board. This base class contains all the initialisation
# and utility methods as well as some necessary overrides for the cmd.Cmd class.
class BaseCommands(cmd.Cmd):
    base_prompt: str = '\r>>> '
    doc_leader: str = (
        '================================================================')
    doc_header: str = (
        'Execute "%magic" commands at the micropython prompt, eg: %ls /\n'
        'Use "%%" to enter multiple command mode.\n'
        'Further help is available for the following commands:\n'
        + doc_leader)
    ruler = ''  # Cmd.ruler is broken if doc_header is multi-line
    remote_cmds = (  # Commands that complete filenames on the board
        'fs', 'ls', 'cat', 'edit', 'touch', 'mv', 'cp', 'rm', 'get',
        'cd', 'mkdir', 'rmdir', 'echo')
    dir_cmds = (  # Commands that complete on directory names
        'cd', 'mkdir', 'rmdir', 'mount', 'lcd')
    noglob_cmds = (  # Commands that have no completion
        'eval', 'exec', 'alias', 'unalias', 'set')

    def __init__(self, board: Board):
        self.colour             = AnsiColour()
        self.multi_cmd_mode     = False
        self.shell_mode         = False
        self.alias:  dict[str, str] = {}    # Command aliases
        self.params: dict[str, Any] = {}    # Params we can use in prompt
        self.names:  dict[str, str] = {}    # Map device unique_ids to names
        self.lsspec: dict[str, str] = {}    # Extra colour specs for %ls
        self.board              = board
        self.colour             = AnsiColour()
        self.prompt             = self.base_prompt
        self.prompt_fmt         = ('{bold-cyan}{id} {yellow}{platform}'
                                   ' ({free}){bold-blue}{pwd}> ')
        self.prompt_colour      = 'cyan'  # Colour of the short prompt
        self.command_colour     = 'reset'  # Colour of the commandline
        self.output_colour      = 'reset'  # Colour of the command output
        self.alias:  dict[str, str] = {}    # Command aliases
        self.params: dict[str, Any] = {}    # Params we can use in prompt
        self.names:  dict[str, str] = {}    # Map device unique_ids to names
        self.lsspec: dict[str, str] = {}    # Extra colour specs for %ls
        readline.set_completer_delims(' \t\n>;')

        # Cmd.cmdloop() overrides completion settings in ~/.inputrc
        # We can disable this by setting completekey=''
        super().__init__(completekey='')
        # But then we need to load the completer function ourselves
        self.old_completer = readline.get_completer()
        readline.set_completer(self.complete)  # type: ignore

        # Load the readline history file
        self.history_file = os.path.expanduser(HISTORY_FILE)
        if os.path.isfile(self.history_file):
            readline.read_history_file(self.history_file)

    def load_command_file(self, file: str) -> bool:
        'Read commands from "file" first in home folder then local folder.'
        for rcfile in [os.path.expanduser('~/' + file), file]:
            lines = []
            try:
                # Load and close file before processing as cmds may force
                # re-write of file (eg. ~/.mpr-thing.options)
                with open(rcfile, 'r') as f:
                    lines = list(f)
            except OSError:
                pass
            for i, line in enumerate(lines):
                try:
                    self.onecmd(line)
                except Exception as err:
                    print(f"Error loading {rcfile} on line {i + 1}: {line.strip()}")
                    print(f"  {type(err).__name__}: {err}")
            return True
        return False

    def load_options_file(self) -> None:
        if not hasattr(self, 'options_loaded') or not self.options_loaded:
            self.load_command_file(OPTIONS_FILE)
            self.options_loaded = True

    def load_rc_file(self) -> None:
        if not hasattr(self, 'rcfile_loaded') or not self.rcfile_loaded:
            self.load_command_file(RC_FILE)
            self.rcfile_loaded = True

    # Load board hook code and params for the prompt
    def load_hooks(self) -> None:
        'Load/reload the helper code onto the micropython board.'
        if not hasattr(self, 'hooks_loaded') or not self.hooks_loaded:
            self.board.load_helper()
            self.board.exec(f'_helper.localtime_offset = {-time.timezone}')
            self.hooks_loaded = True

    def reset_hooks(self) -> None:
        self.hooks_loaded = False

    def load_board_params(self) -> None:
        'Initialise the board parameters - used in the longform prompt'
        if 'id' in self.params:
            return
        # Load these parameters only once for each board
        device_name = self.board.device_name()
        self.params['device'] = device_name
        self.params['dev'] = \
            re.sub(  # /dev/ttyUSB1 -> u1
                r'^/dev/tty(.).*(.)$', r'\1\2',
                re.sub(  # COM2 -> c2
                    r'^COM([0-9]+)$', r'c\1',
                    device_name.lower()))
        with catcher():
            self.params['platform'] = self.board.eval(
                'import sys;print("\\"{}\\"".format(sys.platform))')
        with catcher():
            self.params['unique_id'] = self.board.eval(
                'from machine import unique_id;'
                'print("\\"{}\\"".format(unique_id().hex(":")))')
        with catcher():
            self.params.update(      # Update the board params from uos.uname()
                self.board.eval("print(repr(eval(\"dict{}\".format(uos.uname()))).replace(\"'\", '\"'))"))
        self.params['id'] = self.params['unique_id'][-8:]  # Last 3 octets
        # Add the ansi colour names
        self.params.update(
            {c: self.colour.ansi(c) for c in self.colour.colour})

    def set_prompt(self) -> None:
        "Set the prompt using the prompt_fmt string."
        self.load_board_params()
        pwd, alloc, free = '', 0, 0
        with catcher():
            pwd, alloc, free = self.board.eval('_helper.pr()')

        free_pc = round(100 * free / (alloc + free)) if alloc > 0 else 0
        free_delta = max(0, self.params.get('free', free) - free)

        # Update some dynamic info for the prompt
        self.params['pwd']        = pwd
        self.params['free_delta'] = free_delta
        self.params['free']       = free
        self.params['free_pc']    = free_pc
        self.params['lcd']        = os.getcwd()
        self.params['lcd3']       = '/'.join(os.getcwd().rsplit('/', 3)[1:])
        self.params['lcd2']       = '/'.join(os.getcwd().rsplit('/', 2)[1:])
        self.params['lcd1']       = '/'.join(os.getcwd().rsplit('/', 1)[1:])
        self.params['name']       = self.names.get(    # Look up name for board
            self.params['unique_id'], self.params['id'])
        prompt_colours = {
            'free':     ('green' if free_pc > 50 else
                         'yellow' if free_pc > 25 else
                         'red'),
            'free_pc':  ('green' if free_pc > 50 else
                         'yellow' if free_pc > 25 else
                         'red'),
        }
        prompt_map = {
            k: self.colour(prompt_colours.get(k, ''), v)
            for k, v in self.params.items()}

        self.prompt = (
            # Make GNU readline calculate the length of the colour prompt
            # correctly. See readline.rl_expand_prompt() docs.
            re.sub(
                '(\x1b\\[[0-9;]+m)', '\x01\\1\x02',
                # Make colour reset act like a colour stack
                self.colour.colour_stack(
                    # Build the prompt from prompt_fmt (set with %set cmd)
                    self.prompt_fmt.format_map(prompt_map)))
                + self.colour.ansi(self.command_colour))

    def print_files(self, files: Iterable[RemotePath], opts: str) -> None:
        '''Print a file listing (long or short style) from data returned
        from the board.'''
        # Pretty printing for files on the board
        files = list(files)
        if not files:
            return
        columns = shutil.get_terminal_size().columns
        if 'l' in opts:
            # Long listing style - data is a list of filenames
            for f in files:
                if f.mtime < 40 * 31536000:  # ie. before 2010
                    f.mtime += 946684800   # Correct for epoch=2000 on uPython
                size = f.size if not f.is_dir() else 0
                t = time.strftime(
                    '%c',
                    time.localtime(f.mtime)).replace(' 0', '  ')[:-3]
                filename = self.colour.file(f.name, dir=f.is_dir())
                print(f'{size:9d} {t} {filename}')
        else:
            # Short listing style - data is a list of filenames
            if (len(files) < 20 and
                    sum(len(f.name) + 2 for f in files) < columns):
                # Print all on one line
                for f in files:
                    print(self.colour.file(f.name, dir=f.is_dir()), end='  ')
                print('')
            else:
                # Print in columns - by row
                w = max(len(f.name) for f in files) + 2
                spaces = ' ' * w
                cols = columns // w
                for (i, f) in enumerate(files):
                    n = i + 1
                    print(
                        self.colour.file(f.name, dir=f.is_dir()),
                        spaces[len(f.name):], sep='',
                        end=('' if n % cols and n < len(files) else '\n'))

    def do_include(self, args: Argslist) -> None:
        for arg in args:
            self.load_command_file(arg)

    def do_shell(self, args: Argslist) -> None:
        """
        Execute shell commands from the "%" prompt as well, eg:
            %!date"""
        if args and len(args) == 2 and args[0] == 'cd':
            os.chdir(args[1])
            return

        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            names: list[tuple[str, Path]] = []
            new_args: list[str] = []
            for arg in args:
                if arg.startswith(":"):
                    basename = Path(arg[1:]).name
                    dest = Path(tmpdir) / basename
                    self.board.get(arg[1:], tmpdir)
                    names.append((arg[1:], dest))
                    new_args.append(str(dest))
                else:
                    new_args.append(arg)

            os.system(' '.join(new_args))   # TODO: Use interactive shell

            # for arg, dest in names:
            #     remote.board.put(str(dest), arg)

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
            alias, value = arg.split('=', maxsplit=1)
            if not alias or not value:
                print(f'Invalid alias: "{arg}"')
                continue
            self.alias[alias] = value

        # Now, save the aliases in the options file
        self.save_options()

    def do_unalias(self, args: Argslist) -> None:
        """
        Delete aliases which has been set with the % alias command:
            %unalias ll [...]"""
        for arg in args:
            del self.alias[arg]
        self.save_options()

    def do_set(self, args: Argslist) -> None:  # noqa: C901 too complex
        if not args:
            print(f'set prompt="{self.prompt_fmt}"')
            print(f'set promptcolour="{self.prompt_colour}"')
            print(f'set commandcolour="{self.command_colour}"')
            print(f'set outputcolour="{self.output_colour}"')
            print(f'set name="{self.names[self.params["unique_id"]]}"')
            print(f'set names=\'{json.dumps(self.names)}\'')
            print(f'set lscolour=\'{json.dumps(self.lsspec)}\'')
            return

        for arg in args:
            try:
                key, value = arg.split('=', maxsplit=1)
            except ValueError:
                key, value = '', ''
            if not key or not value:
                print("%set: invalid option setting:", arg)
                continue
            if key == 'prompt':
                saved = self.prompt_fmt
                try:
                    self.prompt_fmt = value
                    backup = self.prompt
                    self.set_prompt()   # Check for errors in the prompt
                    self.prompt = backup
                except KeyError as err:   # Restore the old prompt_fmt
                    print('%set prompt: Invalid key in prompt:', err)
                    self.prompt_fmt = saved
            elif key in ['promptcolour', 'promptcolor']:
                ansi = self.colour.ansi(value)
                if ansi[0] == '\x1b':
                    self.prompt_colour = value
                else:
                    print("%set: invalid colour:", value)
            elif key in ['commandcolour', 'commandcolor']:
                ansi = self.colour.ansi(value)
                if ansi[0] == '\x1b':
                    self.command_colour = value
                else:
                    print("%set: invalid colour:", value)
            elif key in ['outputcolour', 'outputcolor']:
                ansi = self.colour.ansi(value)
                if ansi[0] == '\x1b':
                    self.output_colour = value
                else:
                    print("%set: invalid colour:", value)
            elif key == 'names':
                try:
                    self.names.update(json.loads(value))
                except ValueError as err:
                    print('%set:', err)
            elif key == 'name':
                self.load_board_params()
                self.names[self.params['unique_id']] = value
            elif key in ['lscolour', 'lscolor']:
                d: dict[str, str] = {}
                d.update(json.loads(value))
                for k, v in d.items():
                    colour = self.colour.ansi(v)
                    if colour[0] != '\x1b':
                        print('%set: unknown colour:', v)
                        continue
                    self.lsspec[k.lstrip('*')] = v
                self.colour.spec.update(self.lsspec)
            elif key == 'debug':
                self.board.debug = int(value)
            else:
                print("%set: unknown key:", key)
        self.save_options()

    def save_options(self) -> None:
        'Save the options in a startup file.'
        if not hasattr(self, 'options_loaded') or not self.options_loaded:
            return  # Don't save if we are reading from the options file
        with open(OPTIONS_FILE if os.path.isfile(OPTIONS_FILE) else
                  os.path.expanduser('~/' + OPTIONS_FILE), 'w') as f:
            f.write(
                '# Edit with caution: will be overwritten by mpr-thing.\n')
            f.write(f'set prompt="{self.prompt_fmt}"\n')
            f.write(f'set promptcolour="{self.prompt_colour}"\n')
            f.write(f'set commandcolour="{self.command_colour}"\n')
            f.write(f'set outputcolour="{self.output_colour}"\n')
            f.write(f'set names=\'{json.dumps(self.names)}\'\n')
            f.write(f'set lscolour=\'{json.dumps(self.lsspec)}\'\n')
            for name, value in self.alias.items():
                f.write(f'alias "{name}"="{value}"\n')

    def help_set(self) -> None:
        print(inspect.cleandoc("""
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
        """))
        print((
            f'\nThese options will be automatically saved in ~/{OPTIONS_FILE}\n'
            f'or ./{OPTIONS_FILE} (if it exists).\n'
            f'\nPrompts are python format strings and may include:\n    '),
            end='')
        self.load_board_params()
        for i, k in enumerate(self.params.keys()):
            print(f"{'{' + k + '}':15}", end='' if (i + 1) % 5 else '\n    ')
        print('\n')
        print(inspect.cleandoc("""
        Where:
            {device/dev}: full or short name for the serial device
            {sysname/nodename/release/version/machine}: set from uos.uname()
            {unique_id/id} from machine.unique_id() (id is last 3 octets)
            {colour/bold-colour}: insert an ANSI colour sequence
            {reset}: pop the colour stack
            {bold/normal/underline/reverse}: insert an ANSI text sequence
            {pwd}: current working directory on board
            {free/_pc}: the current free heap memory in bytes/percentage
            {lcdn}: last n parts of local working directory
            {name}: name of current board or {id} if name is not set

        Completion of parameter names is supported by hitting the TAB key."""))

    def default(self, line: str) -> bool:
        'Process any commandlines not matching builtin commands.'
        if not self.multi_cmd_mode and line.strip() == "%":
            # User typed '%%': Enter command line mode
            print(
                'Enter magic commands (try "help" for a list)\n'
                'Type "quit" or ctrl-D to return to micropython:')
            self.multi_cmd_mode = True
            return not self.multi_cmd_mode
        elif (self.multi_cmd_mode and
                line.strip() in ('exit', 'quit', 'EOF')):
            # End command line mode - typed "quit", "exit" or ctrl-D
            self.prompt = self.base_prompt
            self.multi_cmd_mode = False
            if line.strip() == 'EOF':
                print()
            return not self.multi_cmd_mode

        if line.strip().startswith('#'):
            return not self.multi_cmd_mode        # Ignore comments

        print(f'Unknown command: "{line.strip()}"')
        return not self.multi_cmd_mode

    def do_help(self, args: Argslist) -> None:     # type: ignore
        'List available commands with "help" or detailed help with "help cmd".'
        # Need to override Cmd.do_help since we abuse the args parameter
        if not args:
            super().do_help('')
            return
        arg = args[0]
        try:
            func = getattr(self, 'help_' + arg)
        except AttributeError:
            try:
                doc = getattr(self, 'do_' + arg).__doc__
                if doc:
                    self.stdout.write(inspect.cleandoc(doc))
                    self.stdout.write('\n')
                    return
            except AttributeError:
                pass
            self.stdout.write("%s\n" % str(self.nohelp % (arg,)))
            return
        func()

    # Command line parsing, splitting and globbing
    def completedefault(                            # type: ignore
            self, word: str, line: str, begidx: int, endidx: int) -> Argslist:
        'Perform filename completion on "word".'
        cmd         = line.split()[0]
        sep         = word.rfind('/')
        dir, word   = word[:sep + 1], word[sep + 1:]
        if (cmd == 'set' and 'prompt' in line) or cmd == 'echo':
            # Complete on board params, eg: set prompt="{de[TAB]
            sep = word.rfind('{')
            prefix, word = word[:sep + 1], word[sep + 1:]
            return (
                [prefix + k for k in self.params if k.startswith(word)]
                if sep >= 0 else [])
        elif cmd in self.noglob_cmds:
            # No filename completion for this command
            return []
        elif cmd in self.remote_cmds:
            # Execute filename completion on the board.
            lsdir = self.board.ls_dir(dir or '.') or []
            files = [
                dir + str(f) + ('/' if f.is_dir() else '')
                for f in lsdir if f.name.startswith(word)]
        else:
            # Execute filename completion on local host
            try:
                _, dirs, files = next(os.walk(dir or '.'))
                files = [dir + f for f in files if f.startswith(word)]
                files.extend(dir + f + '/' for f in dirs if f.startswith(word))
                files.sort()
            except OSError as err:
                print(OSError, err)
                return []

        # Return all filenames or only directories if requested
        return (
            [f for f in files if f.endswith('/')]
            if cmd in self.dir_cmds else files)

    def glob_remote(self, word: str) -> Iterable[str]:
        'Expand glob patterns in the filename part of "path".'
        if '*' not in word and '?' not in word:
            return (f for f in [])  # type: ignore
        sep = word.rfind('/')
        dir, word = word[:sep + 1] or '.', word[sep + 1:]
        files = self.board.ls_dir(dir) or []
        return (                    # Just return the generator
            ("" if dir == "." else dir) + str(f)
            for f in files
            if str(f)[0] != '.' and fnmatch.fnmatch(str(f), word))

    def glob(self, args: Argslist) -> Iterable[str]:
        remote_glob = args[0] in self.remote_cmds
        no_glob = args[0] in self.noglob_cmds
        yield args[0]
        for arg in args[1:]:
            if arg:
                at_least_one = False
                if not no_glob:
                    for f in (self.glob_remote(arg) if remote_glob else
                              glob.iglob(arg)):
                        at_least_one = True
                        yield f
                if not at_least_one:
                    yield arg   # if no match - just return the glob pattern

    def split(self, line: str) -> Argslist:
        'Split the command line into tokens.'
        # punctuation_chars=True ensures semicolons can split commands
        lex = shlex.shlex(line, None, True, True)
        lex.wordchars += ':'
        return list(lex)

    def expand_alias(self, args: Argslist) -> Argslist:
        if not args or args[0] not in self.alias:
            return args

        alias = self.alias[args.pop(0)]

        # Set of arg indices to be consumed by fmt specifiers: {}, {:23}, ...
        used = set(range(len(re.findall(r'{(:[^}]+)?}', alias))))
        # Add args consumed by {3}, {6:>23}, ...
        used.update(
            int(n) for n in re.findall(r'{([0-9]+):?[^}]*}', alias))

        # Expand the alias: can include format specifiers: {}, {3}, ...
        new_args = self.split(alias.format(*args))
        # Add any unused args to the end of the line
        new_args.extend(arg for n, arg in enumerate(args) if n not in used)

        return new_args

    def process_args(self, args: Argslist) -> Argslist:
        'Expand aliases, macros and glob expansions.'

        # Get the first command if multiple commands on one line.
        # Push the rest of the args back onto the command queue
        # eg: ls /lib ; rm /main.py
        def split_semicolons(args: Argslist) -> Argslist:
            # Split argslist by semicolons
            if ';' not in args:
                return args
            argslist = (  # [[arg1, ...], [arg1,...], ...]
                list(l) for key, l in
                itertools.groupby(args, lambda arg: arg == ';') if not key)
            # Get args for the first subcommand
            args = next(argslist) if args and args[0] != ';' else []
            # Push the rest of the args back onto the cmd queue
            self.cmdqueue[0:0] = list(argslist)  # type: ignore
            return args

        args = split_semicolons(args)
        args = self.expand_alias(args)
        args = split_semicolons(args)   # Alias may expand to include ';'
        args = list(self.glob(args))    # Expand glob patterns

        return args

    # Override some control functions in the Cmd class
    def preloop(self) -> None:
        self.options_loaded: bool
        self.rcfile_loaded:  bool
        self.load_hooks()     # Load the helper code onto the board
        self.load_options_file()
        self.load_rc_file()
        if not self.multi_cmd_mode:
            self.prompt = (
                self.colour(self.prompt_colour, self.base_prompt)
                + self.colour.ansi(self.command_colour) + '%')

    def postloop(self) -> None:
        print(self.base_prompt, end='')  # Re-print the micropython prompt

    def onecmd(self, line: str) -> bool:
        """Override the default Cmd.onecmd()."""
        print(self.colour.ansi("reset"), end="")
        if isinstance(line, list):
            # List of str is pushed back onto cmdqueue in self.split()
            args = line
        else:  # line is a string
            if self.multi_cmd_mode:  # Discard leading '%' in multi-cmd mode
                if line and line.startswith('%') and not line.startswith('%%'):
                    line = line[1:]
                    readline.replace_history_item(1, line)
            readline.write_history_file(self.history_file)

            # A command line read from the input
            if self.shell_mode:
                line = "shell " + line
            cmd, arg, line = self.parseline(line)
            if not line:
                return self.emptyline()
            if not cmd:
                return self.default(line)

            # Split the command line into a list of args
            args = list(self.split(line))

        args = self.process_args(args)  # Expand aliases, macros and globs
        cmd, *args = args or ['']
        self.lastcmd = ''
        func = getattr(self, 'do_' + cmd, None)
        if func:
            ret: bool = func(args)
        else:
            ret = self.default(' '.join([cmd, *args]))
        return ret

    def postcmd(self, stop: Any, line: str) -> bool:
        if self.multi_cmd_mode:
            self.set_prompt()       # Setup our complicated prompt
        # Exit if we are in single command mode and no commands in the queue
        return not self.multi_cmd_mode and not self.cmdqueue

    def run(self, prefix: bytes = b"%") -> None:
        'Catch exceptions and restart the Cmd.cmdloop().'
        stop = False
        self.shell_mode = (prefix == b"!")
        while not stop:
            stop = True
            try:
                self.cmdloop()
            except KeyboardInterrupt:
                stop = not self.multi_cmd_mode
                print()
            except Exception as err:
                print("Error in command:", err)
                # raise
                stop = not self.multi_cmd_mode
        self.shell_mode = False

    def emptyline(self) -> bool:   # Else empty lines repeat last command
        return not self.multi_cmd_mode
