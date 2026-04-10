"""Sans-I/O helpers for building byte-stream protocol parsers.

The package separates buffering from protocol logic so parsers can be written as
small generator coroutines that consume bytes incrementally and emit parsed
events.
"""

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

version = '0.10'

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
    """Raised when the input stream ends before the current event is complete.

    The `partial` attribute contains the unread bytes that belong to the
    unfinished event.
    """

    partial: bytes

    def __init__(self, partial: bytes):
        super().__init__('Incomplete data')
        self.partial = partial


class StreamClosedException(Exception):
    """Raised to signal that the stream ended cleanly at an event boundary."""

    pass


class Receiver:
    """Wrap a parser coroutine and feed byte chunks into it.

    A receiver owns the running parser instance, keeps track of whether it is
    still open, and closes automatically when the parser finishes or the stream
    is closed.
    """

    def __init__(self, g: Parser):
        self._generator = g
        self.open = True
        next(g)

    def send(self, data: Chunk) -> None:
        """Send a chunk of bytes to the underlying parser coroutine."""

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
    """Turn a parser function into a receiver factory.

    Calling the decorated function creates a `Receiver` instance that accepts
    incoming data chunks and passes them into the parser.
    """

    def receiver_factory(emit: Emitter[T], *args: P.args, **kwargs: P.kwargs) -> Receiver:
        return Receiver(parser(emit, *args, **kwargs))

    return receiver_factory  # type: ignore[return-value]


class Reader:
    """Buffer incoming bytes and provide incremental parsing helpers.

    `Reader` keeps unread data between `send()` calls and exposes generator-based
    methods for reading fixed-size fields, unpacking structs, and consuming data
    until a separator is found.
    """

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
        """Discard already consumed bytes and realign reader offsets."""

        offset = self.pos
        self.buf = self.buf[offset:]
        self.pos = 0
        self._event_start -= offset

    def begin_event(self) -> None:
        """Mark the start of the next event.

        This boundary is used to distinguish a clean end of stream from EOF in
        the middle of an event.
        """

        if self._eof:
            raise StreamClosedException

        self._event_start = self.pos

    def handle_eof(self, allow_partial: bool = False) -> bytearray:
        """Handle end-of-stream input.

        Returns unread buffered data when `allow_partial` is true. Otherwise
        raises `IncompleteError` if EOF does not occur at an event boundary, or
        `StreamClosedException` if it does.
        """

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
        """Read exactly `size` bytes.

        Use `yield from` to wait until enough input has been buffered, then
        return those bytes and advance the read position.
        """

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
        """Read and unpack the next fixed-size `struct.Struct` value.

        Use `yield from` to wait until enough input has been buffered, then
        unpack the value and advance the read position.
        """

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
        """Read up to the next `separator`.

        Use `yield from` to wait until the separator is buffered, then return the
        bytes before it or including it, depending on `include`. The read
        position always advances past the separator. If `allow_partial` is true,
        EOF returns the unread tail when no separator is found.
        """

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
    """Collect events emitted by a receiver and return them from `send()`.

    This is a convenience wrapper around `Receiver` for cases where returning
    emitted events as a list is easier than providing a callback.
    """

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
        """Return whether the underlying receiver is still accepting input."""

        return self._receiver.open

    def send(self, data: Chunk) -> List[T]:
        """Send input to the receiver and return events emitted for that chunk."""

        self._receiver.send(data)
        if self._events:
            events = self._events[:]
            self._events.clear()
        else:
            events = []
        return events
