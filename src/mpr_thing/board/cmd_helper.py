# MIT License
# Copyright (c) 2021 @glenn20
# This file contains code to be loaded onto the micropython board
# pylint: skip-file  # pylint ignores the additional micropython typings

import gc
import os

from micropython import const

IS_DIR = const(0x4000)


class _MagicHelper:
    @staticmethod
    def basename(f): return f[f.rstrip("/").rfind("/") + 1 :]

    @staticmethod
    def _stat(f):
        try:
            s = os.stat(f)
            return [s[0], s[6], s[8]]
        except OSError: return []

    def stat(self, f):
        print(self._stat(f))

    def ls_files(self, files):
        # {"f1": [s0, s1, s2], "f2": [s0, s1, s2], ...
        print({f: self._stat(f) for f in files if f})

    def ls_dir(self, d):
        print([f[0] + ("/" if (f[1] & IS_DIR) else "") for f in os.ilistdir(d)])  # type: ignore

    def ls(self, files, R, long):
        # {"": {"files[0]": [s0, s1, s2], "files[1]": [s0, s1, s2], ...}
        #  "dir":  {"f1": [s0, s1, s2], "f2": [s0..], ..},
        #  "dir2": {"f1": [s0, s1, s2], "f2": [s0..], ..}, ...}
        ls = {f: self._stat(f) for f in files if f}
        print('{"":', ls, end="")
        # If recursive, subdirs will be added to end of "dirs" as we go.
        sep1 = ","
        while files:
            d = files.pop()
            m = ls.get(d, self._stat(d))
            if not m or not m[0] & IS_DIR: continue # skip ordinary files
            print(f'{sep1}"{d}":{{ ', end="")
            sep2 = ""
            for f, m, *_ in os.ilistdir(d):  # type: ignore
                p = f"{d}/{f}"
                if R and m & IS_DIR: files.append(p)  # Add dir to list for processing
                s = self._stat(p) if long else [m]
                print(f'{sep2}"{f}":{s}', end="")
                sep2 = ","
            print("}")
            sep1 = ","
        print("}")

    # Using a fixed buffer reduces heap allocation
    _buf = None

    def cp_file(self, f1, f2, v):
        if v: print(f2)
        if self._buf is None: self._buf = bytearray(1024)
        with open(f1, "rb") as f1, open(f2, "wb") as f2:
            while (n := f1.readinto(self._buf)) > 0:
                f2.write(self._buf[:n])  # type: ignore

    def cp_dir(self, d1, d2, v):  # d1 & d2 must end in "/"
        if v: print(d2)
        try: os.mkdir(d2[:-1])
        except OSError: pass
        for f, m, *_ in os.ilistdir(d1):  # type: ignore
            if m & IS_DIR: self.cp_dir(d1 + f + "/", d2 + f + "/", v)
            else: self.cp_file(d1 + f, d2 + f, v)

    def cp(self, files, dirs, dest, v):  # dirs and dest must end in "/"
        for f in files: self.cp_file(f, dest + self.basename(f), v)
        for f in dirs: self.cp_dir(f, dest + self.basename(f), v)

    def rm(self, files, v):
        for f in files:
            if os.stat(f)[0] & IS_DIR:
                self.rm((f"{f}/{i[0]}" for i in os.ilistdir(f)), v)  # type: ignore
                os.rmdir(f)
            else:
                os.remove(f)
            if v: print(f)

    def complete(self, base, word):
        print([w for w in (dir(base) if base else dir()) if w.startswith(word)])

    def pr(self):  # Return some dynamic values for the command prompt
        print([os.getcwd(), gc.mem_alloc(), gc.mem_free()])  # type: ignore


_helper = _MagicHelper()
