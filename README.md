# mpr-thing
Some derived works from Damien George's (@dpgeorge) `mpremote` tool for
micropython.

## Tools

### mpr-thing

A tool to extend Damien George's excellent mpremote tool. I really like his
clever soluton to the micropython dev workflow.

I extended Damien's mpr tool with:
- Execute local shell commands from the micropython prompt using
  `!shell command` escapes: eg.
  - `!ls *.py`
  - `!bash`: Escape to an interactive bash shell ("exit" or crtl-D to return)
  - `!cd dir`: Change working directory on the local host (use os.chdir())
- Execute shell-like command sequences on the board from the micropython
  prompt using `%magic` sequences, including filename and directory completion.
  These include the "mpremote" command list and some others inspired
  by Dave Hyland's (@dhylands) **rshell**, including:
  - `%put file.py .`, `%get main.py`, `%cat boot.py`, `%ls /lib`
  - `%mount .`, `%umount`: Using Damien's virtual FS to mount local
    directory on board.
  - `%cd /remote`
  - `%lcd ..`: change the working directory on the local host
  - `%ls /lib`
  - `%edit /main.py`: Copy file from board, edit (using ${EDITOR:-/bin/vi})
    and copy back.
  - `%time set local/utc`, `%time`: Set/get the board RTC.
  - `%gc`: prints free mem before/after gc.collect()
  - `%help` or `%?`: print list of available magic commands
  - `%help command`: print help on `command`
  - `%%`: Enters multiple-magic command mode
- Minor fixes as I discovered them (see the commit history).

This tool requires that Damien's mpremote tool is installed:
```pip install mpremote```

Warning: to make this work I override the `do_repl_main_loop` function
in the `mpremote` module and use some ugly hackery with terminal handling:
eg. micropython and the escapes have separate command histories.
