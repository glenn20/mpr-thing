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
import subprocess
import tempfile
import time
from pathlib import Path

from mpremote_path import Board
from mpremote_path import MPRemotePath as MPath
from mpremote_path.util import mpfs, mpfsops

from .base_commands import Argslist, BaseCommands


def _opt_args(args: Argslist) -> tuple[str, Argslist]:
    'Extract options from args list and return ("-opts", args).'
    opts, new_args = "", []
    for arg in args:
        if arg.startswith("-"):
            opts += arg
        else:
            new_args.append(arg)
    return (opts, new_args)


# The commands we support at the remote command prompt.
# Inherits from base_commands.Commands which contains all the
# initialisation and utility methods and overrides for cmd.Cmd class.
class RemoteCmd(BaseCommands):
    "A class to run commands on the micropython board."

    board: Board
    last_free: int

    def __init__(self, board: Board):
        self.board = board
        self.long_prompt = (
            "{bold-cyan}{name} {yellow}{platform} (free:{free}){bold-blue}{pwd}> "
        )
        self.last_free = 0
        super().__init__()
        MPath.connect(self.board)  # Connect the MPRemotePath class to the board
        mpfs.name_formatter = self.colour.pathname
        mpfs.path_formatter = self.colour.path

    # File commands
    def do_fs(self, args: Argslist) -> None:
        """
        Emulate the mpremote command line argument filesystem operations:
            %fs [cat,ls,cp,rm,mkdir.rmdir] [args,...]"""
        if not args:
            print("%fs: No fs command provided.")
            return
        fs_cmd = {
            "cat": self.do_cat,
            "ls": self.do_ls,
            "cp": self.do_cp,
            "rm": self.do_rm,
            "mkdir": self.do_mkdir,
            "rmdir": self.do_rmdir,
        }.get(args[0])
        if not fs_cmd:
            print("%fs: Invalid fs command:", args[0])
            return
        fs_cmd(args[1:])

    def do_ls(self, args: Argslist) -> None:
        """
        List files on the board:
            %ls [-[lR]] [file_or_dir1 ...]
        The file listing will be colourised the same as "ls --color". Use the
        "set" command to add or change the file listing colours."""
        opts, args = _opt_args(args)
        with self.board.raw_repl():  # Wrap all ops in a top-level raw_repl
            mpfs.ls(args or ["."], "l" in opts, "r" in opts.lower())

    def do_cat(self, args: Argslist) -> None:
        """
        List the contents of files on the board:
            %cat file [file2 ...]"""
        mpfs.cat(args)

    def do_edit(self, args: Argslist) -> None:
        """
        Copy a file from the board, open it in your editor, then copy it back:
            %edit file1 [file2 ...]"""
        with self.board.raw_repl():
            for arg in args:
                with tempfile.TemporaryDirectory() as tmpdir:
                    src = MPath(arg)
                    dst = Path(tmpdir) / src.name
                    try:
                        mpfs.get(src, dst)
                    except ValueError:
                        dst.touch()
                    editor = os.environ.get("EDITOR", "/usr/bin/editor")
                    print(f"Waiting for editor '{editor} {dst}' to finish...")
                    ret = subprocess.run(
                        f"{editor} {dst}", shell=True, capture_output=True
                    )
                    if ret.returncode == 0:
                        mpfs.put(dst, src)
                    else:
                        print(f"Error running editor {editor}:\n{ret.stderr.decode()}")
                        print("Changes not saved to the board.")

    def do_touch(self, args: Argslist) -> None:
        """
        Create a file on the board:
            %touch file [file2 ...]"""
        with self.board.raw_repl():
            for arg in args:
                mpfs.touch(arg)

    def do_mv(self, args: Argslist) -> None:
        """
        Rename/move a file or directory on the board:
            %mv old new
            %mv *.py /app"""
        opts, args = _opt_args(args)
        with self.board.raw_repl():
            files, dest = (MPath(f) for f in args[:-1]), MPath(args[-1])
            src, dst = mpfsops.check_files("mv", files, dest, opts)
            if dst:
                mpfs.mv(src, dst)

    def do_cp(self, args: Argslist) -> None:
        """
        Make a copy of a file or directory on the board, eg:
            %cp [-r] existing new
            %cp *.py /app"""
        opts, args = _opt_args(args)
        with self.board.raw_repl():
            files, dest = (MPath(f) for f in args[:-1]), MPath(args[-1])
            src, dst = mpfsops.check_files("cp", files, dest, opts)
            if dst:
                mpfs.cp(src, dst)

    def do_rm(self, args: Argslist) -> None:
        """
        Delete files from the board:
            %rm [-r] file1 [file2 ...]"""
        opts, args = _opt_args(args)

        with self.board.raw_repl():
            mpfs.rm(args, "r" in opts)

    @staticmethod
    def _is_remote(filename: str, pwd: str) -> bool:
        "Is the file on a remote mounted filesystem."
        return filename.startswith("/remote") or (
            pwd.startswith("/remote") and not filename.startswith("/")
        )

    def do_get(self, args: Argslist) -> None:
        """
        Copy a file from the board to a local folder:\n
            %get [-n] file1 [file2 ...] [:dest]
        If the last argument start with ":" use that as the destination folder.
        """
        opts, args = _opt_args(args)
        with self.board.raw_repl():
            pwd: str = str(MPath.cwd())
            files: list[str] = []
            for f in args:
                if not f.startswith(":") and self._is_remote(f, pwd):
                    print("get: skipping files in /remote mounted folder:", f)
                else:
                    files.append(f)
            dest = files.pop()[1:] if files[-1].startswith(":") else "."
            mpfs.get(files, dest)

    def do_put(self, args: Argslist) -> None:
        """
        Copy local files to the current folder on the board:
            %put file [file2 ...] [:dest]
        If the last argument start with ":" use that as the destination folder.
        """
        opts, args = _opt_args(args)
        if not args:
            print("%put: Must provide at least one file or directory to copy.")
        with self.board.raw_repl():
            pwd: str = str(MPath.cwd())
            dest = args.pop()[1:] if args[-1].startswith(":") else pwd
            if self._is_remote(dest, pwd):
                print(f"%put: do not put files into /remote mounted folder: {pwd}")
                return
            mpfs.put(args, dest)

    # Directory commands
    def do_cd(self, args: Argslist) -> None:
        """
        Change the current directory on the board (with os.setpwd()):
            %cd /lib"""
        arg = args[0] if args else "/"
        print(mpfs.cd(arg))

    def do_pwd(self, args: Argslist) -> None:
        """
        Print the current working directory on the board:
            %pwd"""
        if args:
            print("pwd: unexpected args:", args)
        print(mpfs.cwd())

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
            mpfs.mkdir(arg)

    def do_rmdir(self, args: Argslist) -> None:
        """
        Delete/remove a directory on the board (if it is empty)
            %rmdir /test"""
        for arg in args:
            mpfs.rmdir(arg)

    # Execute code on the board
    def do_exec(self, args: Argslist) -> None:
        """
        Exec the python code on the board, eg.:
            %exec print(34 * 35)
        "\\n" will be substituted with the end-of-line character, eg:
            %exec 'print("one")\\nprint("two")'"""
        response = self.board.exec(" ".join(args).replace("\\n", "\n"))
        if response:
            print(response)

    def do_eval(self, args: Argslist) -> None:
        """
        Eval and print the python code on the board, eg.:
            %eval 34 * 35"""
        response = self.board.eval(" ".join(args))
        print(response)

    def do_run(self, args: Argslist) -> None:
        """
        Load and run local python files on the board:
            %run file1.py [file2.py ...]"""
        for arg in args:
            try:
                buf = Path(arg).read_text()
                self.board.exec(buf)
            except OSError as err:
                print(OSError, err)

    def do_echo(self, args: Argslist) -> None:
        """
        Echo a command line after file pattern and parameter expansion.
        Eg:
           %echo "Files on {name}:" *.py
           %echo "Free memory on {name} is {green}{free}{reset} bytes."

        where parameters are the same as for "set prompt=" (See "help set").
        Hit the TAB key after typing '{' to see all the available parameters.
        """
        opts, args = _opt_args(args)
        print(
            " ".join(args).format_map(self.parameters), end="" if "n" in opts else "\n"
        )

    # Board commands
    def do_uname(self, args: Argslist) -> None:
        """
        Print information about the hardware and software:
            %uname"""
        if args:
            print("uname: unexpected args:", args)
        print(
            "Micropython {nodename} ({unique_id}) "
            "{version} {sysname} {machine}".format_map(self.parameters)
        )

    def do_time(self, args: Argslist) -> None:
        """
        Set or print the time on the board:
            %time set       : Set the RTC clock on the board from local time
            %time set utc   : Set the RTC clock on the board from UTC time
            %time           : Print the RTC clock time on the board"""
        if args and args[0] == "set":
            self.board.check_clock(set_clock=True, utc="utc" in args)
        time_list = self.board.eval("time.localtime()") + (-1,)  # is_dst = unknown
        print(time.asctime(time.struct_time(time_list)))

    def do_mount(self, args: Argslist) -> None:
        """
        Mount a local folder onto the board at "/remote" as a Virtual
        FileSystem:
            %mount [folder]   # If no folder specified use '.'"""
        # Don't use relative paths - these can change if we "!cd .."
        opts, args = _opt_args(args)
        path = Path(args[0] if args else ".").resolve()
        if path.is_dir():
            with self.board.raw_repl() as mpremote:
                mpremote.mount_local(str(path), unsafe_links="l" in opts)
            print(f"Mounted local folder {args} on /remote")
            print(mpfs.cwd())
        else:
            print("%mount: No such directory:", path)

    def do_umount(self, args: Argslist) -> None:
        """
        Unmount any Virtual Filesystem mounted at \"/remote\" on the board:
            %umount"""
        if args:
            print("umount: unexpected args:", args)
        if str(mpfs.cwd()).startswith("/remote"):
            print(mpfs.cd("/"))
        with self.board.raw_repl() as mpremote:
            mpremote.umount_local()
        print("Unmounted /remote")

    def do_free(self, args: Argslist) -> None:
        """
        Print the free and used memory:
            %free"""
        verbose = "1" if args and args[0] == "-v" else ""
        self.board.exec(f"from micropython import mem_info; print(mem_info({verbose}))")

    def do_df(self, args: Argslist) -> None:
        """
        Print the free and used flash storage:
            %df [dir1, dir2, ...]"""
        df_list: list[tuple[str, int, int, int]] = []
        with self.board.raw_repl():
            for d in args or ["/"]:
                _, bsz, tot, free, *_ = self.board.eval(f"os.statvfs({str(d)!r})")
                df_list.append((str(d), tot * bsz, (tot - free) * bsz, free * bsz))
        print(
            f"{'':10} {'Bytes':>9} {'Used':>9} "
            f"{'Available':>9} {'Use':>3}% {'Mounted on'}"
        )
        for name, total, used, free in df_list:
            pc = round(100 * used / total)
            print(f"{name:10} {total:9d} {used:9d} {free:9d} {pc:3d}% {name}")

    def do_gc(self, args: Argslist) -> None:
        """
        Run the micropython garbage collector on the board to free memory.
        Will also print the free memory before and after gc:
            %gc"""
        if args:
            print("gc: unexpected args:", args)
        before, after = self.board.exec_eval(
            "import gc;_b=gc.mem_free();gc.collect();print([_b,gc.mem_free()])"
        )
        print("Before GC: Free bytes =", before)
        print("After  GC: Free bytes =", after)

    def initialise_board(self) -> bool:
        "Initialise the micropython environment on the board."
        # The %magic commands assume these modules have been imported.
        self.board.exec(
            "import os, sys, gc, time; from machine import unique_id as _unique_id"
        )
        return True

    def set_static_board_parameters(self) -> None:
        "Update the parameters used in the longform prompt."
        self.parameters["device"] = self.board.device_name
        self.parameters["dev"] = self.board.short_name
        self.parameters["platform"], self.parameters["unique_id"] = self.board.eval(
            "(sys.platform, _unique_id().hex())"
        )
        self.parameters.update(self.board.eval('eval(f"dict{os.uname()}")'))
        unique_id = self.parameters.get("unique_id", "")
        self.parameters["id"] = unique_id
        self.parameters["name"] = self.device_names.get(  # Look up name for board
            self.parameters["unique_id"], self.parameters["id"]
        )
        self.last_free = self.board.eval("gc.mem_free()")

    def update_dynamic_board_parameters(self) -> None:
        "Update the dynamic parameters used in the longform prompt."
        # Calculate dynamic quantities for the prompt
        pwd, alloc, free = self.board.eval(
            "(os.getcwd(), gc.mem_alloc(), gc.mem_free())"
        )
        free_percent = round(100 * free / (alloc + free))
        free_colour = (
            "green" if free_percent > 50 else
            "yellow" if free_percent > 25 else
            "red"
        )  # fmt: skip
        # Update dynamic quantities for the prompt
        self.parameters.update(
            {
                "pwd": pwd,
                "free": self.colour(free_colour, free),
                "free_pc": f"{self.colour(free_colour, free_percent)}%",
                "free_delta": f"{self.last_free - free:+d}",
                "time_ms": self.cmd_time,
                "lcd": str(cwd := Path.cwd()),  # Current working directory
                "lcd1": str(Path(*cwd.parts[-1:])),  # Last part of cwd
                "lcd2": str(Path(*cwd.parts[-2:])),  # Last two parts of cwd
                "lcd3": str(Path(*cwd.parts[-3:])),  # Last three parts of cwd
            }
        )
        self.last_free = free

    # Override the base class initialise() method to connect to the board
    # @override
    def initialise(self) -> bool:
        "Initialise the connection to the micropython board."
        if self.initialised:
            return False
        self.initialise_board()
        self.set_static_board_parameters()
        # Update the parameters used in the longform prompt
        super().initialise()
        return True

    def reinitialise_board(self) -> None:
        "Mark the board as uninitialised."
        self.initialised = False

    # Override the base class set_prompt() method to update the prompt
    # @override
    def set_prompt(self) -> None:
        "Set the prompt using the prompt_fmt string."
        if self.multi_cmd_mode:
            # Update the prompt variables with some dynamic info from board
            self.update_dynamic_board_parameters()
        super().set_prompt()
