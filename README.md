# mpr-thing
Some derived works from Damien George's (@dpgeorge) `mpremote` tool for
micropython.

## Tools

### mpr-thing

A tool to extend Damien George's (@dpgeorge) excellent mpremote tool. I really
like his clever solution to the micropython dev workflow.

However, I do like to have access to some convenience commands at the python
prompt when working with my boards - like the ipython `%magic` commands, but
for managing files and stuff on the board.

This tool uses Damien's `mpremote` tool and adds:

- Execute local shell commands from the micropython prompt using `!shell
  command` escapes: eg.
  - `!ls *.py`
  - `!bash`: escape to an interactive bash shell ("exit" or crtl-D to return)
  - `!cd dir`: change working directory on the local host (uses `os.chdir()`)
- Execute shell-like command sequences on the board from the micropython
  prompt using `%magic` sequences, including filename and directory completion
  and file globbing. These include the `mpremote`/`pyboard` command list and
  some others inspired by Dave Hyland's (@dhylands) **rshell**, including:
  - `%mount [dir]`, `%umount`: use Damien's virtual FS to mount local
    directory on board.
  - `%ls -lR /lib`: colourised listing of files on the board (uses your
    color-ls settings).
  - `%cat boot.py`, `%edit main.py`, `%mv f1.py f2.py`,
    `%cp -r /lib /lib2`, `%rm -r /lib2`,
  - `%put app/ file.py`, `%put app/ :/lib`, `%get /app/ /lib/ main.py`,
  - `%edit /main.py`: copy file from board, edit (using ${EDITOR:-/bin/vi})
    and copy back.
  - `%cd /lib`, `%pwd`, `%mkdir /app`, `%rmdir /app`
  - `%lcd ..`: change the working directory on the local host (same as `!cd
    ..`).
  - `%time set local/utc`, `%time`: set/get the board RTC.
  - `%df`, `%free`: print used and free storage or memory
  - `%uname`: print information about the board, OS and device.
  - `%gc`: prints free mem before/after gc.collect().
  - `%exec print(23 * 45)`, `%eval 23 * 45`: Execute python code on the board.
    - `\n` is replaced by end-of-line char before sending to board, eg:
    - `%exec "import machine\nprint(machine.adc(machine.Pin(31)).read())"`
  - `%alias ll='ls -l' connect='exec "network.WLAN(0).connect(\"{}\", \"{}\")"'`
    - You can use `{}` or `{2}` format specifiers to consume arguments when
      you use the alias: eg: `%connect ssid password`. Any arguments which are
      not consumed by format specifiers will be added to the command after
      expanding the alias, eg: `%ll /lib`.
  - `%help`, `%?`, `%help command`: print available magic commands or help on
    command.
  - `;` is used to separate commands on one line: eg. `%cd /app; ls *.py`
  - `ctrl-R`: Toggle DTR on the serial port (reboots some boards, eg
    ESP32/8266).
  - `%%`: Enters multiple-magic command mode with configurable colour
    prompt
  - `%set option=value`:
    Set and save some options:
    - `%set prompt="{cyan}{name}@{dev}-{sysname}-({free}){blue}{pwd}> "`:
      Set the prompt for multi-command mode as you like, eg:
      - `"{cyan}{dev}:{platform}({free}){yellow}{pwd} % "` ->
       `u0:esp32(100880)/lib % `
      - Can select from `{dev}`, `{platform}`, `{unique_id}`, `{id}`,
        `{nodename}`, `{free}`, `{free_pc}` (mem_free in %), `{release}`,
        `{version}`, ...
    - `%set promptcolour=bold-green`:
      Change the colour of the prompt for `%magic` commands
    - `%set name=node05`:
      Set and save the name of the current board (for use in prompt).
    - `%set names='{"ab:cd:ef:01:23:45": "node01", ...}'`:
      Update the mapping of all device unique_ids and names (as json string).
    - `%set lscolour='{"di": "bold-blue", "*.py": "bold-cyan"}'`:
      Add extra colour specs (as json) for `%ls` file listings.

This tool requires that Damien's mpremote tool is installed:
```pip install mpremote```

**Warning:** to make this work I override the `do_repl_main_loop` function in
the `mpremote` module and use some hackery with terminal handling: eg.
micropython and the `%magic` commands have separate command histories.

### Mea Culpa

I know that there is no paucity of very cool terminal apps for talking to your
micropython boards. I like the mpremote approach but just wanted to add some
convenience commands and found it easier to merge in some other stuff from
some old cli tools I have. I really didn't mean to re-invent the wheel.
