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
Chunk = Union[bytes, bytearray, memoryview]
Parser = Generator[None, Chunk, None]
ReaderCoro = Generator[None, Chunk, T]
Emitter = Callable[[T], None]

__all__ = [
    'Reader',
    'receiver',
    'Chunk',
    'Parser',
    'Receiver',
    'Emitter',
    'Collector',
    'ReaderCoro',
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


class Receiver:
    def __init__(self, g: Parser):
        self._generator = g
        self.open = True
        next(g)

    def send(self, data: Chunk) -> None:
        if not self.open:
            raise RuntimeError('Cannot send to a closed receiver')

        try:
            self._generator.send(data)
        except StreamClosedException:
            self.open = False
        except StopIteration:
            self.open = False


def receiver(
    parser: Callable[Concatenate[Emitter[T], P], Parser],
) -> Callable[Concatenate[Emitter[T], P], Receiver]:
    def receiver_factory(emit: Emitter[T], *args: P.args, **kwargs: P.kwargs) -> Receiver:
        return Receiver(parser(emit, *args, **kwargs))

    return receiver_factory  # type: ignore[return-value]


class Reader:
    TRUNCATE_SIZE = 1 << 16

    def __init__(self, truncate_size: Optional[int] = None):
        self.buf = bytearray()
        self.pos = 0
        self._event_start = 0
        self._eof = False
        if truncate_size is None:
            truncate_size = self.TRUNCATE_SIZE
        self._truncate_size = truncate_size

    def truncate(self) -> None:
        offset = self.pos
        self.buf = self.buf[offset:]
        self.pos = 0
        self._event_start -= offset

    def start_event(self) -> None:
        if self._eof:
            raise StreamClosedException
        self._event_start = self.pos

    def handle_eof(self, allow_partial: bool = False) -> bytearray:
        rest = self.buf[self.pos :]
        if self._eof:
            raise RuntimeError('Cannot send to a closed receiver')

        self._eof = True
        if rest:
            if allow_partial:
                return rest

            raise IncompleteError(bytes(rest))

        if self.pos != self._event_start:
            raise IncompleteError(bytes(rest))

        raise StreamClosedException

    def read(self, size: int) -> ReaderCoro[bytes]:
        if self.pos > self._truncate_size:
            self.truncate()

        pos = self.pos
        buf = self.buf

        wpos = pos + size
        while len(buf) < wpos:
            data = yield
            if not data:
                self.handle_eof()
            buf.extend(data)

        rv = bytes(buf[pos:wpos])
        self.pos = wpos
        return rv

    def read_struct(self, struct: Struct) -> ReaderCoro[Tuple[Any, ...]]:
        size = struct.size

        if self.pos > self._truncate_size:
            self.truncate()

        pos = self.pos
        buf = self.buf

        wpos = pos + size
        while len(buf) < wpos:
            data = yield
            if not data:
                self.handle_eof()
            buf.extend(data)

        rv = struct.unpack_from(buf, pos)
        self.pos = wpos
        return rv

    def read_until(
        self, separator: bytes, include: bool = False, allow_partial: bool = False
    ) -> ReaderCoro[bytes]:
        if self.pos > self._truncate_size:
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
                rest = self.handle_eof(allow_partial)
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
        receiver_factory: Callable[Concatenate[Emitter[T], P], Receiver],
        *args: P.args,
        **kwargs: P.kwargs,
    ):
        self._events: List[T] = []
        self._receiver = receiver_factory(self._events.append, *args, **kwargs)

    @property
    def open(self) -> bool:
        return self._receiver.open

    def send(self, data: Chunk) -> List[T]:
        self._receiver.send(data)
        if self._events:
            events = self._events[:]
            self._events.clear()
        else:
            events = []
        return events
