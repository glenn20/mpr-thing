# mpr-thing
Some derived works from Damien George's (@dpgeorge) `mpremote` tool for
micropython.

## Tools

### mpr-thing

A tool to extend Damien George's excellent mpremote tool. I really like his
clever solution to the micropython dev workflow.

However, I do like to have access to some convenience commands at the python
prompt when managing my boards - like the ipython %magic commands, but for
managing files and stuff on the board.

This tool uses Damien's mpremote tool and adds:
- Execute local shell commands from the micropython prompt using
  `!shell command` escapes: eg.
  - `!ls *.py`
  - `!bash`: Escape to an interactive bash shell ("exit" or crtl-D to return)
  - `!cd dir`: Change working directory on the local host (use os.chdir())
- Execute shell-like command sequences on the board from the micropython
  prompt using `%magic` sequences, including filename and directory completion.
  These include the `mpremote` command list and some others inspired
  by Dave Hyland's (@dhylands) **rshell**, including:
  - `%mount .`, `%umount`: Using Damien's virtual FS to mount local
    directory on board.
  - `%ls -lR /lib`: Colourised listing of files on the board (uses your
    color-ls settings).
  - `%cat boot.py`, `%edit main.py`, `%mv f1.py f2.py`,
    `%cp -r /lib /lib2`, `%rm -r /lib2`,
  - `%put file.py`, `%get main.py`,
  - `%edit /main.py`: Copy file from board, edit (using ${EDITOR:-/bin/vi})
    and copy back.
  - `%cd /lib`, `%pwd`, `%mkdir /app`, `%rmdir /app`
  - `%lcd ..`: change the working directory on the local host (same as `!cd ..`).
  - `%time set local/utc`, `%time`: Set/get the board RTC.
  - `%df`, `%free`: print used and free storage or memory
  - `%uname`: print information about the board, OS and device.
  - `%gc`: prints free mem before/after gc.collect().
  - `%help` or `%?`: print list of available magic commands.
  - `%help command`: print help on `command`.
  - `%%`: Enters multiple-magic command mode.
  - `ctrl-R`: Toggle DTR on the serial (reboots some boards, eg ESP32/8266).

This tool requires that Damien's mpremote tool is installed:
```pip install mpremote```

Warning: to make this work I override the `do_repl_main_loop` function
in the `mpremote` module and use some ugly hackery with terminal handling:
eg. micropython and the escapes have separate command histories.

#### Mea Culpa

I know that there is no paucity of very cool terminal apps for talking to your
micropython boards. It is just that I like the mpremote approach but just
wanted to add some convenience commands and just found it easier to merge in
some other stuff from some old cli tools I have. I realy didn't mean to
re-invent the wheel
