# MIT License
# Copyright (c) 2021 @glenn20
# This file contains code to be loaded onto the micropython board

import uos, gc

class _MagicHelper:

    @staticmethod
    def path(a, b):
        return '/'.join((a, b))

    @staticmethod
    def is_dir(mode):
        return ((mode & 0x4000) != 0)

    @staticmethod
    def basename(f):
        return f[f.rfind('/')+1:]

    def ls(self, dir, long):
        print("[", end="")
        for f in uos.ilistdir(dir):
            if long:
                s = uos.stat(self.path(dir, f[0]))
                print('("',f[0],'",',s[0],',',s[6],',',s[8],')',sep="",end=",")
            else:
                print('("',f[0],'",',f[1],')',sep="",end=",")
        print("]")

    # Using a fixed buffer reduces heap allocation
    _buf = bytearray(1024)

    def cp_file(self, f1, f2, opts):
        if 'v' in opts: print(f2)
        if 'n' in opts: return
        f1, f2 = open(f1, "rb"), open(f2, "wb")
        n = f1.readinto(self._buf)
        while n > 0:
            f2.write(self._buf, n)
            n = f1.readinto(self._buf)
        f1.close(); f2.close()

    def cp_dir(self, dir, dest, opts):
        if 'r' not in opts:
            print('Can not copy directory', dir, ': Use "%cp -r"')
            return
        dest = self.path(dest, self.basename(dir))
        try:
            if 'v' in opts: print(dest)
            if 'n' not in opts: uos.mkdir(dest)
        except:
            if not self.is_dir(uos.stat(dest)[0]):
                print('Can not overwrite non-directory',
                    dest, 'with directory', dir)
                return
        for f, m, *_ in uos.ilistdir(dir):
            if self.is_dir(m) and 'r' in opts:
                self.cp_dir(self.path(dir, f), dest, opts)
            else:
                f1, f2 = self.path(dir, f), self.path(dest, f)
                self.cp_file(f1, f2, opts)

    def cp(self, files, dest, opts):
        try:
            dest_m = uos.stat(dest)[0]
        except OSError:
            dest_m = 0
        if not self.is_dir(dest_m):
            print("Destination must be a directory.")
            return
        for f in files:
            if self.is_dir(uos.stat(f)[0]):
                if f != dest:
                    self.cp_dir(f, dest, opts)
                else:
                    print('%cp: Skipping: source is same as dest:', files[0])
            else:
                f2 = self.path(dest, self.basename(f))
                self.cp_file(f, f2, opts)

    def mv(self, files, dest, opts):
        try:
            dir_dest = self.is_dir(uos.stat(dest)[0])
        except OSError:
            dir_dest = False
        if len(files) == 1 and not dir_dest:
            if 'v' in opts: print(dest)
            if 'n' not in opts: uos.rename(files[0], dest)
            return
        elif not dir_dest:
            print("Destination must be a directory.")
            return
        for f in files:
            f2 = self.path(dest, self.basename(f))
            if 'v' in opts: print(f2)
            if 'n' not in opts: uos.rename(f, f2)

    def complete(self, base, word):
        print([w for w in (dir(base) if base else dir()) if w.startswith(word)])

    # TODO: see if chdir through tree reduces heap allocation
    def rm(self, files, opts):
        for f in files:
            try: m = uos.stat(f)[0]
            except OSError: print('No such file:', f); break
            if not self.is_dir(m):
                if 'v' in opts: print(f)
                if 'n' not in opts: uos.remove(f)
            else:
                if 'r' in opts:
                    self.rm(
                        (self.path(f, i[0]) for i in uos.ilistdir(f)), opts)
                    if 'v' in opts: print(f)
                    if 'n' not in opts: uos.rmdir(f)
                else:
                    print('Can not remove directory "{}": Use "%rm -r"'
                        .format(f))

    def pr(self):   # Return some dynamic values for the command prompt
        print(
            '("',uos.getcwd(),'",',gc.mem_alloc(),',',gc.mem_free(),')',
            sep='')

_helper = _MagicHelper()
