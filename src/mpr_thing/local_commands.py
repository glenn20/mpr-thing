# commands.py: Support for mpremote-style filesystem commands at the
# micropython prompt using an Ipython-like '%' escape sequence.
#
# MIT License
# Copyright (c) 2021 @glenn20

# For python<3.10: Allow method type annotations to reference enclosing class
# Allow type1 | type2 instead of Union[type1, type2]
# Allow list[str] instead of List[str]
from __future__ import annotations

import os, cmd, traceback
import shlex
from typing import Any, Optional

# Type alias for the list of command arguments
Argslist = list[str]


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
            begidx: int, endidx: int) -> Argslist:
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
            self, text: str, line: str, begidx: int, endidx: int) -> Argslist:
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

    def cmdloop(self, intro: Optional[str] = None) -> None:
        'Trap exceptions from the Cmd.cmdloop().'
        try:
            super().cmdloop(intro)
        except Exception as err:
            print(err)
