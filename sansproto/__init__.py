from struct import Struct
from typing import (
    Any,
    Callable,
    Generator,
    Generic,
    List,
    Optional,
    Tuple,
    TypeVar,
    Union,
)

from .compat import Concatenate, ParamSpec

version = '0.10dev'

P = ParamSpec('P')
T = TypeVar('T')
Data = Union[bytes, bytearray]
Receiver = Generator[None, Data, None]
DataCoro = Generator[None, Data, T]

__all__ = [
    'Reader',
    'stream_receiver',
    'event_receiver',
    'Receiver',
    'DataReceiver',
    'Collector',
    'DataCoro',
    'IncompleteError',
    'StreamClosedException',
]


class IncompleteError(Exception):
    partial: bytes

    def __init__(self, partial: bytes):
        super().__init__('Incomplete data')
        self.partial = partial


class StreamClosedException(Exception):
    pass


class DataReceiver:
    def __init__(self, g: Receiver):
        self._generator = g
        self.open = True
        next(g)

    def send(self, data: Data) -> None:
        if not self.open:
            raise RuntimeError('Receiver got EOF already')

        try:
            self._generator.send(data)
        except StreamClosedException:
            self.open = False
        except StopIteration:
            self.open = False


def stream_receiver(fn: Callable[P, Receiver]) -> Callable[P, DataReceiver]:
    def proto(*args: P.args, **kwargs: P.kwargs) -> DataReceiver:
        return DataReceiver(fn(*args, **kwargs))

    return proto


def event_receiver(
    truncate_size: Optional[int] = None,
) -> Callable[
    [Callable[Concatenate['Reader', P], Receiver]],
    Callable[P, DataReceiver],
]:
    def decorator(
        fn: Callable[Concatenate['Reader', P], Receiver],
    ) -> Callable[P, DataReceiver]:
        @stream_receiver
        def proto(*args: P.args, **kwargs: P.kwargs) -> Receiver:
            reader = Reader(truncate_size=truncate_size)
            while True:
                reader.start_event()
                yield from fn(reader, *args, **kwargs)

        return proto

    return decorator


class Reader:
    TRUNCATE_SIZE = 1 << 16

    def __init__(self, truncate_size: Optional[int] = None):
        self.buf = bytearray()
        self.pos = 0
        self._event_start = 0
        self.eof = False
        if truncate_size is None:
            truncate_size = self.TRUNCATE_SIZE
        self.truncate_size = truncate_size

    def truncate(self) -> None:
        offset = self.pos
        self.buf = self.buf[offset:]
        self.pos = 0
        self._event_start -= offset

    def start_event(self) -> None:
        if self.eof:
            raise StreamClosedException
        self._event_start = self.pos

    def check_buf_is_empty(self, eof: bool = False) -> bytearray:
        rest = self.buf[self.pos :]
        if self.eof:
            raise RuntimeError('Receiver got EOF already')

        self.eof = True
        if rest:
            if eof:
                return rest

            raise IncompleteError(bytes(rest))

        if self.pos != self._event_start:
            raise IncompleteError(bytes(rest))

        raise StreamClosedException

    def read(self, size: int) -> DataCoro[bytes]:
        if self.pos > self.truncate_size:
            self.truncate()

        pos = self.pos
        buf = self.buf

        wpos = pos + size
        while len(buf) < wpos:
            data = yield
            if not data:
                self.check_buf_is_empty()
            buf.extend(data)

        rv = bytes(buf[pos:wpos])
        self.pos = wpos
        return rv

    def read_struct(self, struct: Struct) -> DataCoro[Tuple[Any, ...]]:
        size = struct.size

        if self.pos > self.truncate_size:
            self.truncate()

        pos = self.pos
        buf = self.buf

        wpos = pos + size
        while len(buf) < wpos:
            data = yield
            if not data:
                self.check_buf_is_empty()
            buf.extend(data)

        rv = struct.unpack_from(buf, pos)
        self.pos = wpos
        return rv

    def read_until(
        self, separator: bytes, include: bool = False, eof: bool = False
    ) -> DataCoro[bytes]:
        if self.pos > self.truncate_size:
            self.truncate()

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
                rest = self.check_buf_is_empty(eof)
                self.pos = len(buf)
                if include:
                    return bytes(rest) + separator
                else:
                    return bytes(rest)

            buf.extend(data)

        npos = idx + len(separator)
        rvpos = npos if include else idx
        rv = bytes(buf[pos:rvpos])
        self.pos = npos
        return rv


class Collector(Generic[T]):
    def __init__(
        self,
        proto: Callable[Concatenate[Callable[[T], None], P], DataReceiver],
        *args: P.args,
        **kwargs: P.kwargs,
    ):
        self._events: List[T] = []
        self._receiver = proto(self._events.append, *args, **kwargs)
        self.open = True

    def send(self, data: bytes) -> List[T]:
        self._receiver.send(data)
        self.open = self._receiver.open
        if self._events:
            events = self._events[:]
            self._events.clear()
        else:
            events = []
        return events
