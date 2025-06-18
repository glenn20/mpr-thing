from typing import Any, Callable, Literal, Optional, Union, overload

from serial import Serial

from .transport import Transport

class TransportError(Exception):
    pass

class SerialIntercept(Serial):
    orig_serial: Serial

class SerialTransport(Transport):
    serial: Union[SerialIntercept, Serial]
    mounted: bool

    def __init__(
        self,
        device: str,
        baudrate: int = 115200,
        wait: Union[float, int] = 0,
        exclusive: bool = True,
        timeout: Union[float, int, None] = None,
    ): ...
    def close(self) -> None: ...
    def enter_raw_repl(
        self,
        soft_reset: bool = True,
        timeout_overall: Union[float, int, None] = 10,
    ) -> None: ...
    def exit_raw_repl(self) -> None: ...
    @overload
    def eval(self, expression: str) -> Any: ...
    @overload
    def eval(self, expression: str, parse: Literal[True]) -> Any: ...
    @overload
    def eval(self, expression: str, parse: Literal[False]) -> bytes: ...
    def exec_raw_no_follow(self, command: Union[bytes, str]) -> bytes: ...
    def exec(
        self,
        command: Union[str, bytes],
        data_consumer: Optional[Callable[[bytes], None]] = None,
    ) -> bytes: ...
    def execfile(self, filename: str) -> bytes: ...
    def write_ctrl_d(self, out_callback: Callable[[bytes], None]) -> None: ...
    def mount_local(self, path: str, unsafe_links: bool = False) -> None: ...
    def umount_local(self) -> None: ...
