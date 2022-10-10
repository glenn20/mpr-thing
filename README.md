# mpr-thing

A tool to extend Damien George's (@dpgeorge) excellent mpremote tool. I really
like his clever solution to the micropython dev workflow.

However, I do like to have access to some convenience commands at the python
prompt when working with my boards - like the `ipython %magic` commands, but
for managing files and stuff on the board.

**Mea Culpa:** I know that there is no paucity of very cool terminal apps for
talking to your micropython boards. I like the mpremote approach but just wanted
to add some convenience commands and found it easier to merge in some other
stuff from some old python commandline interface tools I have. Of course, I
found reason to keep adding features over the last year or so. In that sense,
this tool scratches a particular itch of mine - I really didn't mean to
re-invent the wheel. Nonetheless, if you like the power of mpremote and spend
time working at the micropython repl, you may find this useful for you too.

This tool uses the `mpremote` tool, delegating all command line parameters
to mpremote and adds:

- Execute local shell commands from the micropython prompt using `!shell
  command` escapes: eg.

  | Magic command  |  Description |
  |---|---|
  | `!ls *.py` | list all python files in the current directoty on the board |
  | `!bash` | escape to an interactive bash shell (`exit" or crtl-D to return) |
  | `!cd dir` | change working directory on the local host (uses `os.chdir()`) |
  | `!diff app.py :/lib/app.py` | compare `app.py` on local filesystem with `app.py` on the board. |

- Execute shell-like command sequences on the micropython board using `%magic`
  sequences, including filename and directory completion and file globbing.
  These include the `mpremote`/`pyboard` command list and some others,
  including:

  | Magic command  |  Description |
  |---|---|
  | `%mount [dir]`, `%umount` | Use mpremote's virtual FS to mount/unmount local directory on board |
  | `%ls -lR /lib` | Colourised listing of files on the board (uses your color-ls settings) |
  | `%cat boot.py` | |
  | `%edit /main.py` | Copy file from board, edit (using ${EDITOR:-/bin/vi}) and copy back |
  | `%mv f1.py f2.py` | Move files around on the board |
  | `%cp -r /lib /lib2` | Copy the `/lib` directory on the board to `/lib2` |
  | `%rm -r /lib2` |  |
  | `%put app/ file.py`| Put files onto the micropython board from the local computer |
  | `%put app/ :/lib` | - use `:` to signify the destination directory on the board |
  | `%get /app/ /lib/ main.py` | Get files from the micropython board onto the local compyter |
  | `%get /lib/* :local_dir`| - use `:` to signify the destination directory on the local computer |
  | `%cd /lib`, `%pwd` | Change and list the working directory on the board |
  | `%mkdir /app`, `%rmdir /app` | Create and delete directories on the board |
  | `%lcd ..` | Change the working directory on the local host (same as `!cd ..`) |
  | `%time set local` | Set/get the board time from the local system time |
  | `%time set utc` |  |
  | `%time` |  |
  | `%df`, `%free` | Print used and free storage or memory |
  | `%uname` | Print information about the board, OS and device |
  | `%gc` | Prints free mem before/after gc.collect() |
  | `%help`, `%?`, `%help command` | Print available magic commands or help on command. |
  | `%echo "Device {name}/{pwd}/:"` | Print a message to the user (using parameters - see `%set` command). |
  | `%exec print(23 * 45)` | Execute python code on the board |
  | `%eval 23 * 45` | `\n` is replaced by end-of-line char before sending to board, eg: `%exec "import machine\nprint(machine.adc(machine.Pin(31)).read())"` |
  | `%%` | Enters multiple-magic command line mode with configurable colour prompt |
  | `%alias ll='ls -l'` | Create an alias command: use `{}` or `{2}` to consume arguments when you use the alias. eg. `%alias connect='exec "network.WLAN(0).connect(\"{}\", \"{}\")"'` defines a new command: `%connect ssid password`. Any additional arguments which will be added to the command after expanding the alias, eg: `%ll /lib`.
  | `%unalias connect` |  |
  | `%set option=value` | Set and save some options. Changes will be saved in `~/.mpr-thing.options` and loaded each time mpr-thing starts. |
  | `%set prompt="{pwd}> "` | Set the prompt for multi-command mode, eg: `%set prompt="{cyan}{name}@{dev}-{sysname}-({free}){blue}{pwd}> "`. Can use params from `{dev} {platform} {id} {nodename} {free} {pwd} {lcd} ...`. See `%help set` for a full list. |
  | `%set promptcolour=green` | Change the colour of the prompt for `%magic` commands. |
  | `%set name=node05` | Set and save the name of the current board (for use in prompt). |
  | `%set names='{...}'` | Update the mapping of all device unique_ids and names (as json string): eg. `%set names={"ab:cd:ef:01:23:45": "node01", ...}` |
  | `%set lscolour='{...}'` | Add extra colour specs (as json) for `%ls` file listings, eg: `%set lscolour='{"di": "bold-blue", "*.py": "bold-cyan"}'` | Add extra colour specs (as json) for `%ls` file listings. |
  | `ctrl-R` | Toggle DTR on the serial port (reboots some boards, eg ESP32/8266). |
  | `;` | Used to separate commands on one line eg. `%cd /app; ls *.py` |

This tool requires that Damien's mpremote tool is installed:
```pip install mpremote```

**Warning:** to make this work I override the `do_repl_main_loop` function in
the `mpremote` module and use some hackery with terminal handling: eg.
micropython and the `%magic` commands have separate command histories.

**A notable difference between mpr-thing and mpremote: the `cp` command**:
mpr-thing uses `put` and `get` to put files on the board and to get them from
the board. I found it more intuitive to use the mpr-thing `cp` command for
copying files around within the filesystem on the board, rather than for copying
files between the local system and the micropython board (as mpremote does).
