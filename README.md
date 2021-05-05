# mpr-thing
Some derived works from Damien George's mpr tool for micropython.

## Tools

### mpr-thing

A clone of Damien George's excellent mpr tool (relies on pyboard.py). 
I really like his clever soluton to the micropython dev workflow.

I extended Damien's mpr tool with:
- Execute local shell commands from the micropython prompt using `!shell command` escapes: eg. `!ls *.py`.
- Execute shell-like command sequences on the board from the micropython prompt using `%magic` sequences, 
  including filename and directory completion. These include the "mpr" command list and some others inspired
  by Dave Hyland's (@dhylands) **rshell**:
  - `%put file.py .`, `%get main.py`, `%cat boot.py`, `%ls /lib`
  - `%mount .`, `%umount`: Using @dpgeorge's virtual FS to mount local directory on board.
  - `%cd /remote`
  - `%ls /lib`
  - `%edit main.py`: Copy file from board, edit (using %{EDITOR:-/bin/vi}) and copy back.
  - `%time set local/utc`, `%time`: Set/get the board RTC.
  - `%gc`: prints free mem before/after gc.collect()
- Minor fixes as I discovered them (see the commit history).

Warning: ugly hackery with terminal handling to make this all work. eg. micropython and the escapes have 
separate command histories.
