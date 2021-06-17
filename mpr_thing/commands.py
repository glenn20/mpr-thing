#!/usr/bin/env python3

# Copyright (c) 2021 @glenn20

# MIT License

# vscode-fold=2

import os, re, readline, locale, time, cmd, shutil, traceback
import json, inspect, shlex, glob, fnmatch, subprocess, itertools
from typing import (
    Any, Dict, Iterable, Callable, Optional, List, Tuple)

from mpremote.main import do_command_expansion as mpremote_do_command_expansion

from .catcher import catcher, raw_repl
from .board import RemotePath, Board

from colorama import init as colorama_init  # For ansi colour on Windows

colorama_init()

# Set locale for file listings, etc.
locale.setlocale(locale.LC_ALL, '')

HISTORY_FILE = '~/.mpr-thing.history'
OPTIONS_FILE = '.mpr-thing.options'
RC_FILE      = '.mpr-thing.rc'


def partition(
        items: Iterable[Any],
        predicate: Callable[[Any], bool]
        ) -> Tuple[List[Any], List[Any]]:
    a: List[Any]
    b: List[Any]
    a, b = [], []
    for item in items:
        (a if predicate(item) else b).append(item)
    return a, b


class AnsiColour:
    'A class to colourise text with ANSI escape sequences'
    def __init__(self, enable: bool = True):
        self._enable = enable
        # Load the colour specification for the "ls" command
        spec = (
            os.getenv('LS_COLORS') or
            subprocess.check_output(
                'eval `dircolors`; echo -n $LS_COLORS',
                shell=True, text=True) or
            'di=01;34:*.py=01;36:').rstrip(':')  # A fallback color spec
        self.spec = {
            k.lstrip('*'): v for k, v in
            (c.split('=') for c in spec.split(':'))} if spec else {}
        if '.py' not in self.spec:     # A fallback colour for *.py files
            self.spec['.py'] = '01;36'
        # Dict of ansi colour specs by name
        self.colour = {  # ie: {'black': '00;30', ... 'white': '00;37'}
            name: '00;3{}'.format(i)
            for i, name in enumerate((
                'black', 'red', 'green', 'yellow',
                'blue', 'magenta', 'cyan', 'white'))}
        self.colour.update(dict(   # Add the bright/bold colour variants
            ('bold-' + x, '01;' + spec[3:])
            for x, spec in self.colour.items()))
        self.colour['reset'] = '00'
        self.colour['normal'] = '0'
        self.colour['bold'] = '1'
        self.colour['underline'] = '4'
        self.colour['reverse'] = '7'

    def enable(self, enable: bool = True) -> None:
        """Enable or disable colourising of text with ansi escapes."""
        self._enable = enable

    def ansi(self, spec: str, bold: Optional[bool] = None) -> str:
        return '\x1b[{}m'.format(self.bold(self.colour.get(spec, spec), bold))

    def colourise(
            self, spec: str, word: str,
            bold: Optional[bool] = None, reset: str = 'reset') -> str:
        """Return "word" colourised according to "spec", which may be a colour
        name (eg: 'green') or an ansi sequence (eg: '00;32')."""
        if not spec or not self._enable:
            return word
        spec, reset = (
            self.colour.get(spec, spec),
            self.colour.get(reset, reset))
        spec = self.bold(spec, bold)
        return '\x1b[{}m{}\x1b[{}m'.format(spec, word, reset)

    def __call__(
            self, spec: str, word: str,
            bold: Optional[bool] = None, reset: str = 'reset') -> str:
        """Return "word" colourised according to "spec", which may be a colour
        name (eg: 'green') or an ansi sequence (eg: '00;32')."""
        return self.colourise(spec, word, bold, reset)

    def bold(self, spec: str, bold: Optional[bool] = True) -> str:
        """Set the "bold" attribute in an ansi colour "spec" if "bold" is
        True, or unset the attribute if "bold" is False."""
        spec = self.colour.get(spec, spec)
        return (
            spec if bold is None else
            (spec[:1] + ('1' if bold else '0') + spec[2:]))

    def pick(self, file: str, bold: Optional[bool] = None) -> str:
        """Pick a colour for "file" according to the "ls" command."""
        spec = (
            self.spec.get('di', '') if file[-1] == '/' else
            self.spec.get(os.path.splitext(file)[1], ''))
        return self.bold(spec, bold)

    # Return a colour decorated filename
    def file(self, file: str, dir: bool = False, reset: str = '0') -> str:
        """Return "file" colourised according to the colour "ls" command."""
        return (
            self.colourise(self.pick(file), file, reset=reset) if not dir else
            self.dir(file, reset=reset))

    # Return a colour decorated directory
    def dir(self, file: str, reset: str = '0') -> str:
        """Return "dir" colourised according to the colour "ls" command."""
        return self.colourise(self.spec.get('di', ''), file, reset=reset)

    def colour_stack(self, text: str) -> str:
        """Change the way colour reset sequence ("\x1b[0m") works.
        Change any reset sequences to pop the colour stack rather than
        disabling colour altogether."""
        stack = ['0']

        def ansistack(m: Any) -> Any:
            # Return the replacement text for the reset sequence
            colour: str = m.group(2)
            if len(colour) == 5:   # If this is not a colour reset sequence
                stack.append(colour)            # Save on the colour stack
            elif colour == '1':   # Turn on bold
                stack[-1] = '01;' + stack[-1][3:]
            elif colour == '0':   # Turn off bold
                stack[-1] = '00;' + stack[-1][3:]
                colour = stack[-1]
            elif colour == '00':   # If this is a colour reset sequence
                if len(stack) > 0: stack.pop()  # Pop the stack first
                colour = stack[-1]      # Replace with top colour on stack
            return '\x1b[' + colour + 'm'

        return (re.sub('(\x1b\\[)([0-9;]+)(m)', ansistack, text) +
                (self.ansi('reset') if stack else ''))  # Force reset at end


class LocalCmd(cmd.Cmd):
    'A class to run shell commands on the local system.'
    prompt = '\r>>> !'
    doc_header = """Execute a shell command on the local host, eg:
                    !ls
                    !cd ..
                 """

    # Completion of filenames on the local filesystem
    def completedefault(  # type: ignore
            self, match: str, line: str,
            begidx: int, endidx: int) -> List[str]:
        'Return a list of files on the local host which start with "match".'
        try:
            sep = match.rfind('/')
            d, match = match[:sep + 1], match[sep + 1:]
            _, dirs, files = next(os.walk(d or '.'))
            files = [d + f for f in files if f.startswith(match)]
            files.extend(d + f + '/' for f in dirs if f.startswith(match))
            files.sort()
            return files
        except Exception:
            traceback.print_exc()
            return []

    # Completion of directories on the local filesystem
    def complete_cd(
            self, text: str, line: str, begidx: int, endidx: int) -> List[str]:
        'Return a list of directories which start with "match".'
        return [
            f for f in self.completedefault(text, line, begidx, endidx)
            if f.endswith('/')]

    def do_cd(self, line: str) -> None:
        """Change the current directory on the local host:
            !cd ..
        This will call os.chdir() instead of executing in a sub-shell."""
        for arg in shlex.split(line):
            os.chdir(arg)
        print(os.getcwd())

    def default(self, line: str) -> bool:
        os.system(line)  # TODO: Use interactive shell - subprocess
        return True

    def emptyline(self) -> bool:
        return True

    def postcmd(self, stop: Any, line: str) -> bool:
        return True


class RemoteCmd(cmd.Cmd):
    base_prompt: str = '\r>>> '
    doc_header: str = (
        'Execute "%magic" commands at the micropython prompt, eg: %ls /\n'
        'Use "%%" to enter multiple command mode.\n'
        'Further help is available for the following commands:\n'
        '======================================================')
    ruler = ''       # Cmd.ruler is broken if doc_header is multi-line
    # Cmds for which glob (*) expansion and completion happens on the board
    remote_cmds = (
        'fs', 'ls', 'cat', 'edit', 'touch', 'mv', 'cp', 'rm', 'get',
        'cd', 'mkdir', 'rmdir', 'echo')

    def __init__(self, board: Board):
        self.colour             = AnsiColour()
        self.board              = board
        self.write_fn           = board.writer
        self.prompt             = self.base_prompt
        self.prompt_fmt         = ('{bold-cyan}{id} {yellow}{platform}'
                                   ' ({free}){bold-blue}{pwd}> ')
        self.hooks_loaded       = False     # Is the _helper loaded on board
        self.options_loaded     = False
        self.rcfile_loaded      = False
        self.multi_cmd_mode     = False
        self.prompt_colour      = 'yellow'  # Colour of the short prompt
        self.alias: Dict[str, str] = {}     # Command aliases
        self.params: Dict[str, Any] = {}     # Params we can use in prompt
        self.names: Dict[str, str] = {}     # Map device unique_ids to names
        self.lsspec: Dict[str, str] = {}    # Extra colour specs for %ls
        readline.set_completer_delims(' \t\n>;')
        super().__init__()

        # Load the readline history file
        self.history_file = os.path.expanduser(HISTORY_FILE)
        if os.path.isfile(self.history_file):
            readline.read_history_file(self.history_file)

        # Set the completion functions
        # TODO: Wrap this in complete_default - reuse self.remote_cmds
        fname = self.complete_filename
        self.complete_fs        = fname
        self.complete_ls        = fname
        self.complete_cat       = fname
        self.complete_edit      = fname
        self.complete_touch     = fname
        self.complete_mv        = fname
        self.complete_cp        = fname
        self.complete_rm        = fname
        self.complete_get       = fname
        fname = self.complete_directory
        self.complete_cd        = fname
        self.complete_mkdir     = fname
        self.complete_rmdir     = fname
        fname = self.complete_local_filename
        self.complete_put       = fname
        self.complete_run       = fname
        self.complete_shell     = fname
        fname = self.complete_local_directory
        self.complete_mount     = fname
        self.complete_lcd       = fname

    def load_rc_file(self, file: str) -> bool:
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
            for line in lines:
                self.onecmd(line)
            return True
        return False

    def write(self, response: bytes) -> None:
        'Call the console writer for output (convert "str" to "bytes").'
        if response:
            if not isinstance(response, bytes):
                response = bytes(response, 'utf-8')
            self.write_fn(response)

    # File commands
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
                print('{:9d} {} {}'.format(
                    f.size if not f.is_dir() else 0,
                    time.strftime(
                        '%c',
                        time.localtime(f.mtime)).replace(' 0', '  ')[:-3],
                    self.colour.file(f.name, dir=f.is_dir())))
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

    def do_fs(self, args: List[str]) -> None:
        """
        Emulate the mpremote command line argument filesystem operations:
            %fs [cat,ls,cp,rm,mkdir.rmdir] [args,...]"""
        if not args:
            print("%fs: No fs command provided.")
            return
        fs_cmd = {
            'cat':      self.do_cat,
            'ls':       self.do_ls,
            'cp':       self.do_cp,
            'rm':       self.do_rm,
            'mkdir':    self.do_mkdir,
            'rmdir':    self.do_rmdir,
        }.get(args[0])
        if not fs_cmd:
            print('%fs: Invalid fs command:', args[0])
            return
        fs_cmd(args[1:])

    def do_ls(self, args: List[str]) -> None:
        """
        List files on the board:
            %ls [-[lR]] [file_or_dir1 ...]
        The file listing will be colourised the same as "ls --color". Use the
        "set" command to add or change the file listing colours."""
        opts = ''
        if args and args[0][0] == "-":
            opts, *args = args
        filelist = list(self.board.ls(args, opts))
        linebreak = ''
        for i, (dir, files) in enumerate(filelist):
            # TODO: try: if dir and len(....
            if i > 0 and len(filelist) > 2:     # Print the directory name
                print('{}{}:'.format(linebreak, self.colour.dir(dir)))
            if dir or files:
                self.print_files(files, opts)
                linebreak = '\n'

    def do_cat(self, args: List[str]) -> None:
        """
        List the contents of files on the board:
            %cat file [file2 ...]"""
        for arg in args:
            self.board.cat(arg)

    def do_edit(self, args: List[str]) -> None:
        """
        Copy a file from the board, open it in your editor, then copy it back:
            %edit file1 [file2 ...]
        """
        for arg in args:
            import tempfile
            fd, fname = tempfile.mkstemp()
            os.close(fd)
            try:
                self.board.get(arg, fname)
                if 0 == os.system(f'eval ${{EDITOR:-/usr/bin/vi}} {fname}'):
                    self.board.put(fname, arg)
            finally:
                os.remove(fname)

    def do_touch(self, args: List[str]) -> None:
        """
        Create a file on the board:
            %touch file [file2 ...]"""
        for arg in args:
            self.board.touch(arg)

    def do_mv(self, args: List[str]) -> None:
        """
        Rename/move a file or directory on the board:
            %mv old new
            %mv *.py /app"""
        opts = ''
        if args and args[0][0] == "-":
            opts, *args = args
        dest = args.pop()
        self.board.mv(args, dest, opts)

    def do_cp(self, args: List[str]) -> None:
        """
        Make a copy of a file or directory on the board, eg:
            %cp [-r] existing new
            %cp *.py /app"""
        opts = ''
        if args and args[0][0] == "-":
            opts, *args = args
        dest = args.pop()
        self.board.cp(args, dest, opts)

    def do_rm(self, args: List[str]) -> None:
        """
        Delete files from the board:
            %rm [-r] file1 [file2 ...]"""
        opts = ''
        if args and args[0][0] == "-":
            opts, *args = args
        self.board.rm(args, opts)

    def is_remote(self, filename: str, pwd: str) -> bool:
        'Is the file on a remote mounted filesystem.'
        return (filename.startswith('/remote') or
                (pwd.startswith('/remote') and not filename.startswith('/')))

    def do_get(self, args: List[str]) -> None:
        """
        Copy a file from the board to a local folder:\n
            %get [-n] file1 [file2 ...]
            %get -d dest file1  # Copy files to "dest" instead of "."
        """
        opts = ''
        if args and args[0][0] == "-":
            opts, *args = args
        dest = args.pop(0) if 'd' in opts else '.'
        self.load_board_params()
        # Remove files which are on a remote mounted filesystem
        pwd: str = self.params['pwd']
        remote, args = partition(args, lambda x: self.is_remote(x, pwd))
        for filename in remote:
            print("get: skipping /remote mounted folder:", filename)
        self.board.get(args, dest, opts + 'rv')

    def do_put(self, args: List[str]) -> None:
        """
        Copy local files to the current folder on the board:
            %put file [file2 ...] [:dest]
        If the last argument start with ":" use that as the destination folder.
        """
        opts = ''
        if args and args[0][0] == "-":
            opts, *args = args
        self.load_board_params()
        pwd: str = self.params['pwd']
        if pwd.startswith('/remote'):
            print("%put: do not use on mounted folder:", pwd)
            return
        dest = args.pop()[1:] if args[-1].startswith(':') else '.'
        self.board.put(args, dest, opts + 'rv')

    # Directory commands
    def do_cd(self, args: List[str]) -> None:
        """
        Change the current directory on the board (with os.setpwd()):
            %cd /lib"""
        arg = args[0] if args else '/'
        self.board.cd(arg)

    def do_pwd(self, args: List[str]) -> None:
        """
        Print the current working directory on the board:
            %pwd"""
        if args:
            print('pwd: unexpected args:', args)
        print(self.board.pwd())

    def do_lcd(self, args: List[str]) -> None:
        """
        Change the current directory on the local host:
            %lcd ..
        This is the same as:
            !cd .."""
        for arg in args:
            try:
                os.chdir(arg)
                print(os.getcwd())
            except OSError as err:
                print(OSError, err)

    def do_mkdir(self, args: List[str]) -> None:
        """
        Create a new directory on the board:
            %mkdir /test"""
        for arg in args:
            self.board.mkdir(arg)

    def do_rmdir(self, args: List[str]) -> None:
        """
        Delete/remove a directory on the board (if it is empty)
            %rmdir /test"""
        for arg in args:
            self.board.rmdir(arg)

    # Execute code on the board
    def do_exec(self, args: List[str]) -> None:
        """
        Exec the python code on the board, eg.:
            %exec [--no-follow] print(34 * 35)
        "\\n" will be substituted with the end-of-line character, eg:
            %exec 'print("one")\\nprint("two")' """
        self.board.exec(' '.join(args).replace('\\n', '\n'), follow=True)

    def do_eval(self, args: List[str]) -> None:
        """
        Eval and print the python code on the board, eg.:
            %eval 34 * 35"""
        self.board.exec('print({})'.format(' '.join(args)))

    def do_run(self, args: List[str]) -> None:
        """
        Load and run local python files onto the board:
            %run [--no-follow] file1.py [file2.py ...]"""
        follow = True
        if args and args[0] == '--no-follow':
            args = args[1:]
            follow = False
        for arg in args:
            try:
                with open(arg) as f:
                    buf = f.read()
            except OSError as err:
                print(OSError, err)
            else:
                self.board.exec(buf, follow=follow)

    def do_echo(self, args: List[str]) -> None:
        """
        Echo a command line after file pattern expansion:
           %echo *.py Make*"""
        print(' '.join(args))

    # Board commands
    def do_uname(self, args: List[str]) -> None:
        """
        Print information about the hardware and software:
            %uname"""
        if args:
            print('uname: unexpected args:', args)
        self.load_board_params()
        self.write((
            'Micropython {nodename} ({unique_id}) '
            '{version} {sysname} {machine}'
            .format_map(self.params)).encode('utf-8') + b'\r\n')

    def do_time(self, args: List[str]) -> None:
        """
        Set or print the time on the board:
            %time set       : Set the RTC clock on the board from local time
            %time set utc   : Set the RTC clock on the board from UTC time
            %time           : Print the RTC clock time on the board"""
        if args and args[0] == 'set':
            from time import gmtime, localtime
            t = gmtime() if 'utc' in args else localtime()
            rtc_cmds = {
                'esp8266': 'from machine import RTC;RTC().datetime({})',
                'pyb':     'from pyb import RTC;RTC().datetime({})',
                'pycom':   'from machine import RTC;_t={};'
                           'RTC().init((_t[i] for i in [0,1,2,4,5,6]))',
            }
            self.load_board_params()
            fmt = rtc_cmds.get(
                self.params['sysname'],
                'from machine import RTC;RTC().init({})')
            self.board.exec(fmt.format(
                (t.tm_year, t.tm_mon, t.tm_mday, 0,
                    t.tm_hour, t.tm_min, t.tm_sec, 0)))
        from time import asctime
        t = self.board.eval('import utime;print(utime.localtime())')
        self.write(asctime(
            (t[0], t[1], t[2], t[3], t[4], t[5], 0, 0, 0)).encode('utf-8')
            + b'\r\n')

    def do_mount(self, args: List[str]) -> None:
        """
        Mount a local folder onto the board at "/remote" as a Virtual
        FileSystem:
            %mount [folder]   # If no folder specified use '.'"""
        # Don't use relative paths - these can change if we "!cd .."
        path = args[0] if args else '.'
        self.board.mount(path)
        self.write(
            'Mounted local folder {} on /remote\r\n'
            .format(args).encode('utf-8'))
        self.board.exec('print(uos.getcwd())')

    def do_umount(self, args: List[str]) -> None:
        """
        Unmount any Virtual Filesystem mounted at \"/remote\" on the board:
            %umount"""
        if args:
            print('umount: unexpected args:', args)
        self.board.umount()
        self.board.exec('print(uos.getcwd())')

    def do_free(self, args: List[str]) -> None:
        """
        Print the free and used memory:
            %free"""
        verbose = '1' if args and args[0] == '-v' else ''
        self.board.exec(
            'from micropython import mem_info; mem_info({})'.format(verbose))

    def do_df(self, args: List[str]) -> None:
        """
        Print the free and used flash storage:
            %df"""
        if args:
            print('df: unexpected args:', args)
        _, bsz, tot, free, *_ = self.board.eval('print(uos.statvfs("/"))')
        print("{:10} {:>9} {:>9} {:>9} {:>3}% {}".format(
            "", "Bytes", "Used", "Free", "Use", "Mounted on"))
        print("{:10} {:9d} {:9d} {:9d} {:3d}% {}".format(
            "/", tot * bsz, (tot - free) * bsz, free * bsz,
            round(100 * (1 - free / tot)), "/"))

    def do_gc(self, args: List[str]) -> None:
        """
        Run the micropython garbage collector on the board to free memory.
        Will also print the free memory before and after gc:
            %gc"""
        if args:
            print('gc: unexpected args:', args)
        before, after = self.board.eval(
            'from gc import mem_free,collect;'
            'b=mem_free();collect();print([b,mem_free()])')
        print("Before GC: Free bytes =", before)
        print("After  GC: Free bytes =", after)

    # Extra commands
    def do_shell(self, args: List[str]) -> None:
        """
        Execute shell commands from the "%" prompt as well, eg:
            %!date"""
        if args and len(args) == 2 and args[0] == 'cd':
            os.chdir(args[1])
        else:
            os.system(' '.join(args))   # TODO: Use interactive shell

    def do_alias(self, args: List[str]) -> None:
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
        NOTE: "ll" and "lr" are pre-builtin as aliases as above.
        You can not override existing commands with an alias.
        """
        for arg in args:
            print(arg)
            alias, value = arg.split('=', maxsplit=1)
            if not alias or not value:
                print('Invalid alias: "{}"'.format(arg))
                continue
            self.alias[alias] = value

    def do_set(self, args: List[str]) -> None:
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
                with catcher(self.write):
                    self.prompt_fmt = value
                    backup = self.prompt
                    self.set_prompt()   # Check for errors in the prompt
                    self.prompt = backup
                if catcher.exception:   # Restore the old prompt_fmt
                    self.prompt_fmt = saved
            elif key in ['promptcolour', 'promptcolor']:
                ansi = self.colour.ansi(value)
                if ansi[0] == '\x1b':
                    self.prompt_colour = value
                else:
                    print("%set: invalid colour:", self.prompt_colour)
            elif key == 'names':
                try:
                    self.names.update(json.loads(value))
                except ValueError as err:
                    print('%set:', err)
            elif key == 'name':
                self.load_board_params()
                self.names[self.params['unique_id']] = value
            elif key in ['lscolour', 'lscolor']:
                d: Dict[str, str] = {}
                d.update(json.loads(value))
                for k, v in d.items():
                    colour = self.colour.ansi(v)
                    if colour[0] != '\x1b':
                        print('%set: unknown colour:', v)
                        continue
                    self.lsspec[k.lstrip('*')] = v
                self.colour.spec.update(self.lsspec)
            else:
                print("%set: unknown key:", key)
        # Save the options in a startup file
        if not self.options_loaded:  # - unless we are reading the options file
            return
        with open(OPTIONS_FILE if os.path.isfile(OPTIONS_FILE) else
                  os.path.expanduser('~/' + OPTIONS_FILE), 'w') as f:
            f.write(
                '# Edit with caution: will be overwritten by mpr-thing.\n')
            f.write('set prompt="{}"\n'.format(self.prompt_fmt))
            f.write('set promptcolour="{}"\n'.format(self.prompt_colour))
            f.write('set names=\'{}\'\n'.format(json.dumps(self.names)))
            f.write('set lscolour=\'{}\'\n'.format(
                json.dumps(self.lsspec)))

    def help_set(self) -> None:
        if self.do_set.__doc__:
            print(inspect.cleandoc("""
        Set some options, eg:
            %set prompt='{cyan}{name}@{dev}-{sysname}-({free}){blue}{pwd}> '
            %set prompt='{cyan}{name}@{dev}({free}){green}{lcd1}:{blue}{pwd}> '
            %set promptcolour=yellow
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
            '\nThese options will be automatically saved in ~/{0}\n'
            'or ./{0} (if it exists).\n'
            '\nPrompts are python format strings and may include:\n    ')
            .format(OPTIONS_FILE),
            end='')
        self.load_board_params()
        for i, k in enumerate(self.params.keys()):
            print(
                "{:15}".format('{' + k + '}'),
                end='' if (i + 1) % 5 else '\n    ')
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
            {name}: name of current board or {id} if name is not set"""))

    def default(self, line: str) -> bool:
        'Process any commandlines not matching builtin commands.'
        if not self.multi_cmd_mode and line.strip() == "%":
            # User typed '%%': Enter command line mode
            self.write(
                b'Enter magic commands (try "help" for a list)\n'
                b'Type "quit" or ctrl-D to return to micropython:\n')
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

        self.write('Unknown command: "{}"\r\n'.format(line.strip()).encode())
        return not self.multi_cmd_mode

    def do_help(self, args: List[str]) -> None:  # type: ignore
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

    def set_prompt(self) -> None:
        "Set the prompt using the prompt_fmt string."
        self.load_board_params()
        pwd: str
        alloc: int
        free: int
        pwd, alloc, free = self.board.eval('_helper.pr()')

        alloc, free = int(alloc), int(free)
        free_pc = round(100 * free / (alloc + free))
        heap_used = str(max(0, self.params.get('free', free) - free)).rjust(5)

        # Update some dynamic info for the prompt
        self.params['pwd']       = pwd
        self.params['heap_used'] = heap_used
        self.params['free']      = free
        self.params['free_pc']   = free_pc
        self.params['lcd']       = os.getcwd()
        self.params['lcd3']      = '/'.join(os.getcwd().rsplit('/', 3)[1:])
        self.params['lcd2']      = '/'.join(os.getcwd().rsplit('/', 2)[1:])
        self.params['lcd1']      = '/'.join(os.getcwd().rsplit('/', 1)[1:])
        self.params['name']      = self.names.get(    # Look up name for board
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
                    self.prompt_fmt.format_map(prompt_map))))

    # File and directory completion functions
    def complete_filename(
            self, word: str, line: str, begidx: int, endidx: int) -> List[str]:
        'Return a list of filenames on the board starting with "word".'
        sep = word.rfind('/')
        dir, word = word[:sep + 1] or '.', word[sep + 1:]
        files = self.board.ls_dir(dir)
        return [
            str(f) + ('/' if f.is_dir() else '')
            for f in files if f.name.startswith(word)] if files else []

    def complete_directory(
            self, word: str, line: str, begidx: int, endidx: int) -> List[str]:
        'Return a list of directories on the board starting with "match".'
        return [
            f for f in self.complete_filename(word, line, begidx, endidx)
            if f.endswith('/')]

    def complete_local_filename(
            self, word: str, line: str, begidx: int, endidx: int) -> List[str]:
        'Return a list of local filenames starting with "match".'
        try:
            sep = word.rfind('/')
            d, match = word[:sep + 1], word[sep + 1:]
            _, dirs, files = next(os.walk(d or '.'))
            files = [d + f for f in files if f.startswith(match)]
            files.extend(d + f + '/' for f in dirs if f.startswith(match))
            files.sort()
            return files
        except OSError as err:
            print(OSError, err)
        return []

    def complete_local_directory(
            self, word: str, line: str, begidx: int, endidx: int) -> List[str]:
        'Return a list of local directories starting with "match".'
        return [
            f for f in self.complete_local_filename(word, line, begidx, endidx)
            if f.endswith('/')]

    def reload_hooks(self) -> None:
        'Load/reload the helper code onto the micropython board.'
        self.board.load_hooks()
        self.hooks_loaded = True
        self.board.exec('_helper.localtime_offset = {}'.format(-time.timezone))

    def load_board_params(self) -> None:
        'Initialise the board parameters - used in the longform prompt'
        if 'id' in self.params:
            return
        # Load these parameters only once for each board
        self.params = {
            'device':       self.board.pyb.device_name,
            'dev':          re.sub(r'^/dev/tty(.).*(.)$',
                                   r'\1\2',
                                   self.board.pyb.device_name.lower()),
            'platform':     self.board.eval('from sys import platform;'
                                            'print(repr(platform))'),
            'unique_id':    self.board.eval('from machine import unique_id;'
                                            'print(repr(unique_id()))'
                                            ).hex(':'),
        }
        self.params.update(      # Update the board params from uos.uname()
            self.board.eval('print("dict{}".format(uos.uname()))'))
        self.params['id'] = self.params['unique_id'][-8:]  # Last 3 octets
        # Add the ansi colour names
        self.params.update(
            {c: self.colour.ansi(c) for c in self.colour.colour})

    def reset_hooks(self) -> None:
        self.hooks_loaded = False

    # Command line parsing, splitting and globbing
    def glob_remote(self, word: str) -> Iterable[str]:
        'Expand glob patterns in the filename part of "path".'
        sep = word.rfind('/')
        dir, word = word[:sep + 1] or '.', word[sep + 1:]
        if '*' not in word and '?' not in word:
            return (f for f in [])  # type: ignore
        files = self.board.ls_dir(dir)
        return ((                   # Just return the generator
            str(f)
            for f in files
            if str(f)[0] != '.' and fnmatch.fnmatch(str(f), word))
            if files else [])

    def glob(self, args: List[str]) -> Iterable[str]:
        remote_glob = args[0] in self.remote_cmds
        yield args[0]
        for arg in args[1:]:
            if arg:
                at_least_one = False
                for f in (self.glob_remote(arg) if remote_glob else
                          glob.iglob(arg)):
                    at_least_one = True
                    yield f
                if not at_least_one:
                    yield arg   # if no match - just return the glob pattern

    def expand_alias(self, args: List[str]) -> List[str]:
        if not args or args[0] not in self.alias:
            return args

        alias = self.alias[args.pop(0)]

        # Set of arg indices to be consumed by fmt specifiers: {}, {:23}, ...
        used = set(range(len(re.findall(r'{(:[^}]+)?}', alias))))
        # Add args consumed by {3}, {6:>23}, ...
        used.update(
            int(n) for n in re.findall(r'{([0-9]+):?[^}]*}', alias))

        # Expand the alias: can include format specifiers: {}, {3}, ...
        new_args = alias.format(*args).split()
        # Add any unused args to the end of the line
        new_args.extend(arg for n, arg in enumerate(args) if n not in used)

        print(new_args)
        return new_args

    def split(self, line: str) -> List[str]:
        'Split the command line into tokens and expand any glob patterns.'
        # punctuation_chars=True ensures semicolons can split commands
        lex = shlex.shlex(line, None, True, True)
        lex.wordchars += ':'
        args = list(lex)  # List of all arguments, including cmd
        if not args:
            return args

        def split_semicolons(args: List[str]) -> List[str]:
            # Split argslist by semicolons
            argslist = (  # [[arg1, ...], [arg1,...], ...]
                list(l) for key, l in
                itertools.groupby(args, lambda arg: arg == ';') if not key)
            # Get args for the first subcommand
            args = next(argslist) if args and args[0] != ';' else []
            # Push the rest of the args back onto the cmd queue
            self.cmdqueue[0:0] = list(argslist)  # type: ignore
            return args

        args = split_semicolons(args)

        # Expand any aliases
        args = self.expand_alias(args)
        args = split_semicolons(args)

        # Expand mpremote commandline macros
        mpremote_do_command_expansion(args)
        for i, arg in enumerate(args):
            if arg in [
                    'connect', 'disconnect', 'mount', 'eval',
                    'exec', 'run', 'fs']:
                if i > 0 and args[i - 1] != ';':
                    args.insert(i, ';')
        args = split_semicolons(args)

        # Expand any glob patterns on the command line
        args = list(self.glob(args))

        return args

    # Cmd control functions
    def preloop(self) -> None:
        if not self.hooks_loaded:
            self.reload_hooks()     # Load the helper code onto the board
        if not self.options_loaded:
            self.load_rc_file(OPTIONS_FILE)
            self.options_loaded = True
        if not self.rcfile_loaded:
            self.load_rc_file(RC_FILE)
            self.rcfile_loaded = True
        if not self.multi_cmd_mode:
            self.prompt = \
                self.colour(self.prompt_colour, self.base_prompt) + '%'

    def postloop(self) -> None:
        print(self.base_prompt, end='')  # Re-print the micropython prompt

    def onecmd(self, line: str) -> bool:
        """Override the default Cmd.onecmd()."""
        if isinstance(line, list):
            # List of str is pushed back onto cmdqueue in self.split()
            cmd, *args = list(self.glob(line))
        else:  # line is a string
            if self.multi_cmd_mode:  # Discard leading '%' in multi-cmd mode
                if line and line[0] == "%":
                    line = line[1:]
                    readline.replace_history_item(1, line)
            readline.write_history_file(self.history_file)

            # A command line read from the input
            cmd, arg, line = self.parseline(line)
            try:  # Process with split() first to handle ';' separator.
                args = list(self.split(line))
                cmd, *args = args if args else ['']
            except ValueError as err:
                print('%magic command error:', err)
                return False
            if not line:
                return self.emptyline()
            if not cmd:
                return self.default(line)

        self.lastcmd = ''
        func: Callable[[List[str]], bool] = getattr(self, 'do_' + cmd, None)
        if func:
            return func(args)
        else:
            return self.default(' '.join([cmd, *args]))

    def postcmd(self, stop: Any, line: str) -> bool:
        if self.multi_cmd_mode:
            self.set_prompt()       # Setup our complicated prompt
        # Exit if we are in single command mode and no commands in the queue
        return not self.multi_cmd_mode and not self.cmdqueue

    def cmdloop(self, intro: Optional[str] = None) -> None:
        'Add a raw_repl and exception catcher to the Cmd.cmdloop().'
        while True:
            with raw_repl(self.board.pyb, self.write,
                          soft_reset=False, silent=False):
                super().cmdloop(intro)
            if type(catcher.exception) != KeyboardInterrupt:
                break
            print()

    def emptyline(self) -> bool:   # Else empty lines repeat last command
        return not self.multi_cmd_mode
