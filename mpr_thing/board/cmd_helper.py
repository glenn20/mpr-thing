#!/usr/bin/env python3

# Copyright (c) 2021 @glenn20
# MIT License

# This file contains code to be loaded onto the micropython board

# vscode-fold=2

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

    def cp_file(self, f1, f2):
        f1, f2 = open(f1, "rb"), open(f2, "wb")
        n = f1.readinto(self._buf)
        while n > 0:
            f2.write(self._buf, n)
            n = f1.readinto(self._buf)
        f1.close(); f2.close()

    def cp_dir(self, dir, dest, r=0, v=False):
        if r <= 0:
            print('Can not copy directory', dir, ': Increase recursion')
            return
        dest = self.path(dest, self.basename(dir))
        try:
            uos.mkdir(dest)
            if v: print(dest)
        except:
            if not self.is_dir(uos.stat(dest)[0]):
                print('Can not overwrite non-directory',
                    dest, 'with directory', dir)
                return
        for f, m, *_ in uos.ilistdir(dir):
            if self.is_dir(m) and r > 0:
                self.cp_dir(self.path(dir, f), dest, r - 1, v)
            else:
                f1, f2 = self.path(dir, f), self.path(dest, f)
                if v: print(f2)
                self.cp_file(f1, f2)

    def cp(self, files, dest, r=0, v=False):
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
                    self.cp_dir(f, dest, r, v)
                else:
                    print('%cp: Skipping: source is same as dest:', files[0])
            else:
                f2 = self.path(dest, self.basename(f))
                if v: print(f2)
                self.cp_file(f, f2)

    def mv(self, files, dest, v=False):
        try:
            dir_dest = self.is_dir(uos.stat(dest)[0])
        except OSError:
            dir_dest = False
        if len(files) == 1 and not dir_dest:
            if v: print(dest)
            uos.rename(files[0], dest)
            return
        elif not dir_dest:
            print("Destination must be a directory.")
            return
        for f in files:
            f2 = self.path(dest, self.basename(f))
            if v: print(f2)
            uos.rename(f, f2)

    # TODO: see if chdir through tree reduces heap allocation
    def rm(self, files, r=0, v=False):
        for f in files:
            try:
                if v: print(f)
                uos.remove(f)
                continue
            except OSError:
                pass
            try:
                m = uos.stat(f)[0]
            except OSError:
                print('No such file:', f)
                break
            if self.is_dir(m):
                if r > 0:
                    self.rm(
                        (self.path(f, i[0]) for i in uos.ilistdir(f)),
                        r-1, v)
                    if v: print(f)
                    uos.rmdir(f)
                else:
                    print('Can not remove directory "{}": Increase recursion'
                        .format(f))
            else:
                print('Unable to remove:', f)

    import gc

    def pr(self):   # Return some dynamic values for the command prompt
        print(
            '("',uos.getcwd(),'",',gc.mem_alloc(),',',gc.mem_free(),')',
            sep='')

_helper = _MagicHelper()
