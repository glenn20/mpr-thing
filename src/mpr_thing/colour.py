# colour.py: Support for ansi colour escape sequences in the mpr-thing
# prompt.
#
# MIT License
# Copyright (c) 2021 @glenn20

# For python<3.10: Allow method type annotations to reference enclosing class
# Allow type1 | type2 instead of Union[type1, type2]
# Allow list[str] instead of List[str]
from __future__ import annotations

import os
import re
import subprocess
from typing import Any, Optional

from colorama import init as colorama_init  # For ansi colour on Windows

# Ensure colour works on Windows terminals.
colorama_init()


class AnsiColour:
    "A class to colourise text with ANSI escape sequences"

    def __init__(self, enable: bool = True) -> None:
        self._enable = enable
        # Load the colour specification for the "ls" command
        spec = (
            os.getenv("LS_COLORS")
            or subprocess.check_output(
                "eval `dircolors`; echo -n $LS_COLORS", shell=True, text=True
            )
            or "di=01;34:*.py=01;36:"
        ).rstrip(
            ":"
        )  # A fallback color spec
        self.spec = (
            {k.lstrip("*"): v for k, v in (c.split("=") for c in spec.split(":"))}
            if spec
            else {}
        )
        if ".py" not in self.spec:  # A fallback colour for *.py files
            self.spec[".py"] = "01;36"
        # Dict of ansi colour specs by name
        self.colour = {  # ie: {'black': '00;30', ... 'white': '00;37'}
            name: f"00;3{i}"
            for i, name in enumerate(
                ("black", "red", "green", "yellow", "blue", "magenta", "cyan", "white")
            )
        }
        self.colour.update(
            dict(  # Add the bright/bold colour variants
                ("bold-" + x, "01;" + spec[3:]) for x, spec in self.colour.items()
            )
        )
        self.colour["reset"] = "00"
        self.colour["normal"] = "0"
        self.colour["bold"] = "1"
        self.colour["underline"] = "4"
        self.colour["reverse"] = "7"
        self.colour.update(  # Add ansi256 colour specs
            {f"ansi{i}": f"38;5;{i}" for i in range(256)}
        )

    def enable(self, enable: bool = True) -> None:
        """Enable or disable colourising of text with ansi escapes."""
        self._enable = enable

    def ansi(self, spec: str, bold: Optional[bool] = None) -> str:
        spec = self.bold(self.colour.get(spec, spec), bold)
        return f"\x1b[{spec}m"

    def colourise(
        self, spec: str, word: str, bold: Optional[bool] = None, reset: str = "reset"
    ) -> str:
        """Return "word" colourised according to "spec", which may be a colour
        name (eg: 'green') or an ansi sequence (eg: '00;32')."""
        if not spec or not self._enable:
            return word
        spec, reset = (self.colour.get(spec, spec), self.colour.get(reset, reset))
        spec = self.bold(spec, bold)
        return f"\x1b[{spec}m{word}\x1b[{reset}m"

    def __call__(
        self, spec: str, word: str, bold: Optional[bool] = None, reset: str = "reset"
    ) -> str:
        """Return "word" colourised according to "spec", which may be a colour
        name (eg: 'green') or an ansi sequence (eg: '00;32')."""
        return self.colourise(spec, word, bold, reset)

    def bold(self, spec: str, bold: Optional[bool] = True) -> str:
        """Set the "bold" attribute in an ansi colour "spec" if "bold" is
        True, or unset the attribute if "bold" is False."""
        spec = self.colour.get(spec, spec)
        return spec if bold is None else (spec[:1] + ("1" if bold else "0") + spec[2:])

    def pick(self, file: str, bold: Optional[bool] = None) -> str:
        """Pick a colour for "file" according to the "ls" command."""
        spec = (
            self.spec.get("di", "")
            if file[-1] == "/"
            else self.spec.get(os.path.splitext(file)[1], "")
        )
        return self.bold(spec, bold)

    # Return a colour decorated filename
    def file(self, file: str, directory: bool = False, reset: str = "0") -> str:
        """Return "file" colourised according to the colour "ls" command."""
        return (
            self.colourise(self.pick(file), file, reset=reset)
            if not directory
            else self.dir(file, reset=reset)
        )

    # Return a colour decorated directory
    def dir(self, file: str, reset: str = "0") -> str:
        """Return "dir" colourised according to the colour "ls" command."""
        return self.colourise(self.spec.get("di", ""), file, reset=reset)

    def colour_stack(self, text: str) -> str:
        """Change the way colour reset sequence ("\x1b[0m") works.
        Change any reset sequences to pop the colour stack rather than
        disabling colour altogether."""
        stack = ["0"]

        def ansistack(m: Any) -> Any:
            # Return the replacement text for the reset sequence
            colour: str = m.group(2)
            if len(colour) == 5:  # If this is not a colour reset sequence
                stack.append(colour)  # Save on the colour stack
            elif colour == "1":  # Turn on bold
                stack[-1] = "01;" + stack[-1][3:]
            elif colour == "0":  # Turn off bold
                stack[-1] = "00;" + stack[-1][3:]
                colour = stack[-1]
            elif colour == "00":  # If this is a colour reset sequence
                if len(stack) > 0:
                    stack.pop()  # Pop the stack first
                colour = stack[-1]  # Replace with top colour on stack
            return "\x1b[" + colour + "m"

        return re.sub("(\x1b\\[)([0-9;]+)(m)", ansistack, text) + (
            self.ansi("reset") if stack else ""
        )  # Force reset at end
