# MIT License
# Copyright (c) 2021 @glenn20
# This file contains code to be loaded onto the micropython board

import uos, gc
from micropython import const

IS_DIR = const(0x4000)


class _MagicHelper:

    @staticmethod
    def path(a, b):
        return a + ("/" if a and a != "/" else "") + b

    @staticmethod
    def basename(f):
        return f[f.rstrip('/').rfind('/') + 1:]

    @staticmethod
    def stat(f):
        try:
            s = uos.stat(f)
            return [s[0], s[6], s[8]]
        except OSError:
            return []

    def ls_files(self, files):
        print([[f, self.stat(f)] for f in files])

    def ls_dirs(self, dirs, opts):
        print("[", end="")
        dsep = ""
        while dirs:
            d = dirs.pop()
            ds = d.rstrip("/") + "/"  # Ensure ends in a single "/"
            print('{}["{}", ['.format(dsep, d), end="")
            sep = ""
            for f in uos.ilistdir(d):  # type: ignore
                p = ds + f[0]
                if 'R' in opts and f[1] & IS_DIR:
                    dirs.append(p)
                s = self.stat(p) if 'l' in opts else [f[1]]
                print('{}["{}", {}]'.format(sep, f[0], s), end="")
                sep = ","
            print(']]')
            dsep = ","
        print("]")

    # Using a fixed buffer reduces heap allocation
    _buf = None

    def cp_file(self, f1, f2, v, n):
        if v: print(f2)
        if n: return
        if self._buf is None: self._buf = bytearray(1024)
        with open(f1, "rb") as f1, open(f2, "wb") as f2:
            while (n := f1.readinto(self._buf)) > 0:
                f2.write(self._buf[:n])  # type: ignore

    def cp_dir(self, d1, d2, v, n):  # d1 & d2 must end in "/"
        if v: print(d2)
        if not n:
            try: uos.mkdir(d2[:-1])
            except OSError: pass
        for f, m, *_ in uos.ilistdir(d1):  # type: ignore
            if m & IS_DIR:
                self.cp_dir(d1 + f + "/", d2 + f + "/", v, n)
            else:
                self.cp_file(d1 + f, d2 + f, v, n)

    def cp(self, files, dirs, dest, v, n):  # dirs and dest must end in "/"
        for f in files:
            self.cp_file(f, dest + self.basename(f), v, n)
        for f in dirs:
            self.cp_dir(f, dest + self.basename(f), v, n)

    def complete(self, base, word):
        print([w for w in (dir(base) if base else dir()) if w.startswith(word)])

    def rm(self, files, v, n):
        for f in files:
            if not uos.stat(f)[0] & IS_DIR:
                if v: print(f)
                if not n: uos.remove(f)
            else:
                self.rm(((f + "/" + i[0]) for i in uos.ilistdir(f)), v, n)
                if v: print(f)
                if not n: uos.rmdir(f)

    def pr(self):   # Return some dynamic values for the command prompt
        print('[\"{}\",{},{}]'.format(
            uos.getcwd(), gc.mem_alloc(), gc.mem_free()))  # type: ignore


_helper = _MagicHelper()
