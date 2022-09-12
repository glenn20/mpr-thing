# commands.py: Support for mpremote-style filesystem commands at the
# micropython prompt using an Ipython-like '%' escape sequence.
#
# MIT License
# Copyright (c) 2021 @glenn20

# For python<3.10: Allow method type annotations to reference enclosing class
# Allow type1 | type2 instead of Union[type1, type2]
# Allow list[str] instead of List[str]
from __future__ import annotations

import os
from pathlib import Path

from .catcher import catcher
from .board import Board
from .base_commands import Commands, Argslist


class RemoteCmd(Commands):
    'A class to run commands on the micropython board.'
    def __init__(self, board: Board):
        super().__init__(board)

    # File commands
    def do_fs(self, args: Argslist) -> None:
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

    def do_ls(self, args: Argslist) -> None:
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

    def do_cat(self, args: Argslist) -> None:
        """
        List the contents of files on the board:
            %cat file [file2 ...]"""
        for arg in args:
            self.board.cat(arg)

    def do_edit(self, args: Argslist) -> None:
        """
        Copy a file from the board, open it in your editor, then copy it back:
            %edit file1 [file2 ...]
        """
        for arg in args:
            import tempfile
            with tempfile.TemporaryDirectory() as tmpdir:
                basename = Path(arg).name
                dest = Path(tmpdir) / basename
                self.board.get(arg, tmpdir)
                if 0 == os.system(
                        f'eval ${{EDITOR:-/usr/bin/vi}} {str(dest)}'):
                    self.board.put(str(dest), arg)

    def do_touch(self, args: Argslist) -> None:
        """
        Create a file on the board:
            %touch file [file2 ...]"""
        for arg in args:
            self.board.touch(arg)

    def do_mv(self, args: Argslist) -> None:
        """
        Rename/move a file or directory on the board:
            %mv old new
            %mv *.py /app"""
        opts = ''
        if args and args[0][0] == "-":
            opts, *args = args
        dest = args.pop()
        self.board.mv(args, dest, opts)

    def do_cp(self, args: Argslist) -> None:
        """
        Make a copy of a file or directory on the board, eg:
            %cp [-r] existing new
            %cp *.py /app"""
        opts = ''
        if args and args[0][0] == "-":
            opts, *args = args
        dest = args.pop()
        self.board.cp(args, dest, opts)

    def do_rm(self, args: Argslist) -> None:
        """
        Delete files from the board:
            %rm [-r] file1 [file2 ...]"""
        opts = ''
        if args and args[0][0] == "-":
            opts, *args = args
        self.board.rm(args, opts)

    def is_remote(self, filename: str, pwd: str) -> bool:
        # Is the file on a remote mounted filesystem.
        return (
            filename.startswith('/remote') or
            (pwd.startswith('/remote') and not filename.startswith('/')))

    def do_get(self, args: Argslist) -> None:
        """
        Copy a file from the board to a local folder:\n
            %get [-n] file1 [file2 ...] [:dest]
        If the last argument start with ":" use that as the destination folder.
        """
        opts = ''
        if args and args[0][0] == "-":
            opts, *args = args
        self.load_board_params()
        pwd: str = self.params['pwd']
        files: list[str] = []
        for f in args:
            if self.is_remote(f, pwd):
                print("get: skipping /remote mounted folder:", f)
            else:
                files.append(f)
        dest = files.pop()[1:] if files[-1].startswith(':') else '.'
        self.board.get(files, dest, opts + 'rv')

    def do_put(self, args: Argslist) -> None:
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
        dest = args.pop()[1:] if args[-1].startswith(':') else pwd
        if self.is_remote(dest, pwd):
            print("%put: do not use on mounted folder:", pwd)
            return
        self.board.put(args, dest, opts + 'rv')

    # Directory commands
    def do_cd(self, args: Argslist) -> None:
        """
        Change the current directory on the board (with os.setpwd()):
            %cd /lib"""
        arg = args[0] if args else '/'
        self.board.cd(arg)

    def do_pwd(self, args: Argslist) -> None:
        """
        Print the current working directory on the board:
            %pwd"""
        if args:
            print('pwd: unexpected args:', args)
        print(self.board.pwd())

    def do_lcd(self, args: Argslist) -> None:
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

    def do_mkdir(self, args: Argslist) -> None:
        """
        Create a new directory on the board:
            %mkdir /test"""
        for arg in args:
            self.board.mkdir(arg)

    def do_rmdir(self, args: Argslist) -> None:
        """
        Delete/remove a directory on the board (if it is empty)
            %rmdir /test"""
        for arg in args:
            self.board.rmdir(arg)

    # Execute code on the board
    def do_exec(self, args: Argslist) -> None:
        """
        Exec the python code on the board, eg.:
            %exec print(34 * 35)
        "\\n" will be substituted with the end-of-line character, eg:
            %exec 'print("one")\\nprint("two")' """
        self.board.exec(' '.join(args).replace('\\n', '\n'))

    def do_eval(self, args: Argslist) -> None:
        """
        Eval and print the python code on the board, eg.:
            %eval 34 * 35"""
        self.board.exec('print({})'.format(' '.join(args)))

    def do_run(self, args: Argslist) -> None:
        """
        Load and run local python files onto the board:
            %run file1.py [file2.py ...]"""
        for arg in args:
            try:
                with open(arg) as f:
                    buf = f.read()
            except OSError as err:
                print(OSError, err)
            else:
                self.board.exec(buf)

    def do_echo(self, args: Argslist) -> None:
        """
        Echo a command line after file pattern and parameter expansion.
        Eg:
           %echo "Files on {name}:" *.py
           %echo "Free memory on {name} is {green}{free}{reset} bytes."

        where parameters are the same as for "set prompt=" (See "help set").
        Hit the TAB key after typing '{' to see all the available parameters.
        """
        opts, *args = args if args and args[0].startswith("-") else ('', *args)
        print(
            ' '.join(args).format_map(self.params),
            end='' if 'n' in opts else '\n')

    # Board commands
    def do_uname(self, args: Argslist) -> None:
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

    def do_time(self, args: Argslist) -> None:
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
        with catcher(self.board.write):
            t = self.board.eval('import utime;print(utime.localtime())')
            self.write(asctime(
                (t[0], t[1], t[2], t[3], t[4], t[5], 0, 0, 0)).encode('utf-8')
                + b'\r\n')

    def do_mount(self, args: Argslist) -> None:
        """
        Mount a local folder onto the board at "/remote" as a Virtual
        FileSystem:
            %mount [folder]   # If no folder specified use '.'"""
        # Don't use relative paths - these can change if we "!cd .."
        opts = ''
        if args and args[0][0] == "-":
            opts, *args = args
        path = args[0] if args else '.'
        self.board.mount(path, opts)
        self.write(
            'Mounted local folder {} on /remote\r\n'
            .format(args).encode('utf-8'))
        self.board.exec('print(uos.getcwd())')

    def do_umount(self, args: Argslist) -> None:
        """
        Unmount any Virtual Filesystem mounted at \"/remote\" on the board:
            %umount"""
        if args:
            print('umount: unexpected args:', args)
        self.board.umount()
        self.board.exec('print(uos.getcwd())')

    def do_free(self, args: Argslist) -> None:
        """
        Print the free and used memory:
            %free"""
        verbose = '1' if args and args[0] == '-v' else ''
        self.board.exec(
            'from micropython import mem_info; mem_info({})'.format(verbose))

    def do_df(self, args: Argslist) -> None:
        """
        Print the free and used flash storage:
            %df [dir1, dir2, ...]"""
        print("{:10} {:>9} {:>9} {:>9} {:>3}% {}".format(
            "", "Bytes", "Used", "Available", "Use", "Mounted on"))
        for dir in (args or ['/']):
            with catcher(self.board.write):
                _, bsz, tot, free, *_ = self.board.eval(
                    'print(uos.statvfs("{}"))'.format(dir))
                print("{:10} {:9d} {:9d} {:9d} {:3d}% {}".format(
                    dir, tot * bsz, (tot - free) * bsz, free * bsz,
                    round(100 * (1 - free / tot)), dir))

    def do_gc(self, args: Argslist) -> None:
        """
        Run the micropython garbage collector on the board to free memory.
        Will also print the free memory before and after gc:
            %gc"""
        if args:
            print('gc: unexpected args:', args)
        with catcher(self.board.write):
            before, after = self.board.eval(
                'from gc import mem_free,collect;'
                'b=mem_free();collect();print([b,mem_free()])')
            print("Before GC: Free bytes =", before)
            print("After  GC: Free bytes =", after)

    # Extra commands
    def do_shell(self, args: Argslist) -> None:
        """
        Execute shell commands from the "%" prompt as well, eg:
            %!date"""
        if args and len(args) == 2 and args[0] == 'cd':
            os.chdir(args[1])
        else:
            os.system(' '.join(args))   # TODO: Use interactive shell

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
                print('Invalid alias: "{}"'.format(arg))
                continue
            self.alias[alias] = value

        # Now, save the aliases in the options file
        self.save_options()

    def do_unalias(self, args: Argslist) -> None:
        """
        Delete aliases which has been set with the %alias command:
            %unalias ll [...]"""
        for arg in args:
            del self.alias[arg]
        self.save_options()
