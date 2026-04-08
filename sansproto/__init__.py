from struct import Struct
from typing import Any, Callable, Generator, Generic, List, Optional, Tuple, TypeVar, Union

from .compat import ParamSpec

P = ParamSpec('P')
T = TypeVar('T')
Data = Union[bytes, bytearray]
Receiver = Generator[None, Data, None]
DataCoro = Generator[None, Data, T]
DataCall = Callable[[Data], None]

__all__ = ['Reader', 'receiver', 'Receiver', 'DataCall', 'Collector', 'DataCoro', 'IncompleteError']


class IncompleteError(Exception):
    partial: bytes

    def __init__(self, partial: bytearray):
        super().__init__('Incomplete data')
        self.partial = bytes(partial)


def receiver(fn: Callable[P, Receiver]) -> Callable[P, DataCall]:
    def proto(*args: P.args, **kwargs: P.kwargs) -> DataCall:
        g = fn(*args, **kwargs)
        next(g)
        return g.send

    return proto


class Reader:
    TRUNCATE_SIZE = 1 << 16

    def __init__(self, truncate_size: Optional[int] = None):
        self.buf = bytearray()
        self.pos = 0
        self.eof = False
        if truncate_size is None:
            truncate_size = self.TRUNCATE_SIZE
        self.truncate_size = truncate_size

    def check_buf_is_empty(self, eof: bool = False) -> DataCoro[bytearray]:
        rest = self.buf[self.pos :]
        if rest:
            if self.eof:
                raise RuntimeError('Receiver got EOF already')
            self.eof = True
            if eof:
                return rest
            raise IncompleteError(rest)
        else:
            self.eof = True
            yield
            raise RuntimeError('Receiver got EOF already')

    def read(self, size: int) -> DataCoro[bytes]:
        if self.pos > self.truncate_size:
            self.buf = self.buf[self.pos :]
            self.pos = 0

        pos = self.pos
        buf = self.buf

        wpos = pos + size
        while len(buf) < wpos:
            data = yield
            if not data:
                yield from self.check_buf_is_empty()
            buf.extend(data)

        rv = bytes(buf[pos:wpos])
        self.pos = wpos
        return rv

    def read_struct(self, struct: Struct) -> DataCoro[Tuple[Any, ...]]:
        size = struct.size

        if self.pos > self.truncate_size:
            self.buf = self.buf[self.pos :]
            self.pos = 0

        pos = self.pos
        buf = self.buf

        wpos = pos + size
        while len(buf) < wpos:
            data = yield
            if not data:
                yield from self.check_buf_is_empty()
            buf.extend(data)

        rv = struct.unpack_from(buf)
        self.pos = wpos
        return rv

    def read_until(
        self, separator: bytes, include: bool = False, eof: bool = False
    ) -> DataCoro[bytes]:
        if self.pos > self.truncate_size:
            self.buf = self.buf[self.pos :]
            self.pos = 0

        pos = self.pos
        buf = self.buf

        start = pos
        while True:
            idx = buf.find(separator, start)
            if idx >= 0:
                break

            start = max(len(buf) - len(separator) + 1, 0)
            data = yield
            if not data:
                buf = yield from self.check_buf_is_empty(eof)
                if include:
                    return bytes(buf) + separator
                else:
                    return bytes(buf)

            buf.extend(data)

        npos = idx + len(separator)
        rvpos = npos if include else idx
        rv = bytes(buf[pos:rvpos])
        self.pos = npos
        return rv


class Collector(Generic[T]):
    def __init__(self, proto: Callable[[Callable[[T], None]], DataCall]):
        self._events: List[T] = []
        self._data_call = proto(self._events.append)

    def send(self, data: bytes) -> List[T]:
        self._data_call(data)
        if self._events:
            events = self._events[:]
            self._events.clear()
        else:
            events = []
        return events
