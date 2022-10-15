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

from .board import Board
from .base_commands import BaseCommands, Argslist


# The commands we support at the remote command prompt.
# Inherits from base_commands.Commands which contains all the
# initialisation and utility methods and overrides for cmd.Cmd class.
class RemoteCmd(BaseCommands):
    'A class to run commands on the micropython board.'

    def __init__(self, board: Board):
        super().__init__(board)

    @staticmethod
    def _options(args: Argslist) -> tuple[str, Argslist]:
        "Extract options from args list and return (\"-opts\", args)."
        opts, new_args = "", []
        for arg in args:
            if arg.startswith("-"):
                opts += arg
            else:
                new_args.append(arg)
        return (opts, new_args)

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
        opts, args = self._options(args)
        filelist = list(self.board.ls(args, opts))
        linebreak = ''
        first_time = True
        for dir, files in filelist:
            if not first_time and len(filelist) > 2:  # Print the directory name
                print(f'{linebreak}{self.colour.dir(dir)}')
            if dir or files:
                self.print_files(files, opts)
                linebreak = '\n'
            first_time = False

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
        opts, args = self._options(args)
        dest = args.pop()
        self.board.mv(args, dest, opts)

    def do_cp(self, args: Argslist) -> None:
        """
        Make a copy of a file or directory on the board, eg:
            %cp [-r] existing new
            %cp *.py /app"""
        opts, args = self._options(args)
        dest = args.pop()
        self.board.cp(args, dest, opts)

    def do_rm(self, args: Argslist) -> None:
        """
        Delete files from the board:
            %rm [-r] file1 [file2 ...]"""
        opts, args = self._options(args)
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
        opts, args = self._options(args)
        self.load_board_params()
        pwd: str = self.params['pwd']
        files: list[str] = []
        for f in args:
            if self.is_remote(f, pwd):
                print("get: skipping files in /remote mounted folder:", f)
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
        opts, args = self._options(args)
        self.load_board_params()
        pwd: str = self.params['pwd']
        dest = args.pop()[1:] if args[-1].startswith(':') else pwd
        if self.is_remote(dest, pwd):
            print("%put: do not put files into /remote mounted folder:", pwd)
            return
        self.board.put(args, dest, opts + 'rv')

    def do_sync(self, args: Argslist) -> None:
        """
        Sync a local folder to a folder on the board:
            %sync folder :dest
        """
        opts, args = self._options(args)
        if len(args) != 2:
            print("%sync: takes two arguments:")
            return
        src, dest = args
        self.board.sync(src, dest, opts)

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
        response = self.board.exec(' '.join(args).replace('\\n', '\n'))
        if response:
            print(response)

    def do_eval(self, args: Argslist) -> None:
        """
        Eval and print the python code on the board, eg.:
            %eval 34 * 35"""
        response = self.board.exec(f"print({' '.join(args)})")
        print(response)

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
        opts, args = self._options(args)
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
        print(
            'Micropython {nodename} ({unique_id}) '
            '{version} {sysname} {machine}'
            .format_map(self.params))

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
        t = self.board.get_localtime()
        print(asctime(
            (t[0], t[1], t[2], t[3], t[4], t[5], t[6], t[7], 0)))

    def do_mount(self, args: Argslist) -> None:
        """
        Mount a local folder onto the board at "/remote" as a Virtual
        FileSystem:
            %mount [folder]   # If no folder specified use '.'"""
        # Don't use relative paths - these can change if we "!cd .."
        opts, args = self._options(args)
        path = args[0] if args else '.'
        self.board.mount(path, opts)
        print(f'Mounted local folder {args} on /remote')
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
            f'from micropython import mem_info; mem_info({verbose})')

    def do_df(self, args: Argslist) -> None:
        """
        Print the free and used flash storage:
            %df [dir1, dir2, ...]"""
        df_list = self.board.df(args)
        print("{:10} {:>9} {:>9} {:>9} {:>3}% {}".format(
            "", "Bytes", "Used", "Available", "Use", "Mounted on"))
        for name, total, used, free in df_list:
            print("{:10} {:9d} {:9d} {:9d} {:3d}% {}".format(
                name, total, used, free, round(100 * used / total), name))

    def do_gc(self, args: Argslist) -> None:
        """
        Run the micropython garbage collector on the board to free memory.
        Will also print the free memory before and after gc:
            %gc"""
        if args:
            print('gc: unexpected args:', args)
        before, after = self.board.gc()
        print("Before GC: Free bytes =", before)
        print("After  GC: Free bytes =", after)
