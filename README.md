# sansproto

[Sans-I/O][sans-io] helpers for byte stream protocol parsers.

[sans-io]: https://sans-io.readthedocs.io/

## Motivation

Byte stream protocols need to handle messages split across arbitrary chunks. One
`recv()` call can contain half a message, exactly one message, or several messages at
once. `sansproto` keeps that buffering logic in a small `Reader`, so parser code can
describe the protocol shape directly.

The parser stays Sans-I/O: it does not own sockets, files, timeouts, or event loops.
That makes the same parser usable with blocking sockets, asyncio, Trio, tests, or any
other code that can feed it bytes.

## Core Idea

Write a single-event parser as a generator coroutine and decorate it with
`event_receiver()`. Inside the parser, `Reader` methods such as `read()` and
`read_until()` suspend until enough bytes are available. When a complete message is
parsed, call a handler with the event you want to emit.

You can call the decorated parser directly with your own handler, or use `Collector` to
collect the events produced by each chunk.

For lower-level stream parsers, use `stream_receiver` and call `reader.start_event()`
before the code that parses one complete event. `event_receiver()` does that loop for
you.

Send an empty chunk, `b''`, to signal that the input stream is closed. Receivers and
collectors expose an `.open` flag, so callers can stop feeding bytes once EOF has been
accepted. If a partial message is in progress, `Reader` raises `IncompleteError`
instead.

## Performance

Naive ad hoc implementations often copy buffered data repeatedly, for example by
rebuilding the buffer on every incoming chunk or deleting consumed bytes after every
read. `Reader` avoids that pattern: incoming chunks are appended to one `bytearray`,
reads advance an offset, and compaction happens only after the consumed prefix passes a
threshold.

Returned values are copied to `bytes`, which gives callers stable immutable data even as
the internal buffer continues to be reused.

## Examples

Use `Reader` inside an `event_receiver()` coroutine to parse one message. `Collector`
wraps the coroutine and returns any events produced after each chunk.

```python
collector = ...
while collector.open:
    for event in collector.send(sock.recv(65536)):
        handle(event)
```

### Length-prefixed messages

This parser reads a decimal byte length followed by `:`, then reads exactly that many
payload bytes.

```python
from typing import Callable

from sansproto import Collector, Reader, Receiver, event_receiver


@event_receiver()
def parser(reader: Reader, handler: Callable[[str], None]) -> Receiver:
    size = int((yield from reader.read_until(b':')))
    payload = yield from reader.read(size)
    handler(payload.decode())


messages = Collector(parser)

assert messages.send(b'5:he') == []
assert messages.send(b'llo3:') == ['hello']
assert messages.send(b'bye') == ['bye']
```

### Delimited messages

`read_until` can also parse line-oriented protocols. The separator is consumed, but it
is not included in the returned bytes unless `include=True` is passed.

```python
from typing import Callable

from sansproto import Collector, Reader, Receiver, event_receiver


@event_receiver()
def parser(reader: Reader, handler: Callable[[str], None]) -> Receiver:
    line = yield from reader.read_until(b'\n')
    handler(line.decode())


lines = Collector(parser)

assert lines.send(b'hello\nwor') == ['hello']
assert lines.send(b'ld\n') == ['world']
```

### Binary headers

Use `read_struct` with `struct.Struct` when a protocol has fixed-size binary fields.
This parser reads a two-byte big-endian payload size followed by that many payload bytes.

```python
from struct import Struct
from typing import Callable

from sansproto import Collector, Reader, Receiver, event_receiver


header = Struct('!H')


@event_receiver()
def parser(reader: Reader, handler: Callable[[bytes], None]) -> Receiver:
    (size,) = yield from reader.read_struct(header)
    payload = yield from reader.read(size)
    handler(payload)


messages = Collector(parser)

assert messages.send(b'\x00\x05he') == []
assert messages.send(b'llo') == [b'hello']
```

### Composing parsers

Parser helpers can be generator functions too. Use `yield from` to delegate part of the
protocol to a smaller parser and return the parsed value to the caller.

```python
from typing import Callable

from sansproto import Collector, DataCoro, Reader, Receiver, event_receiver


def read_size(reader: Reader) -> DataCoro[int]:
    raw_size = yield from reader.read_until(b':')
    return int(raw_size)


def read_text(reader: Reader, size: int) -> DataCoro[str]:
    payload = yield from reader.read(size)
    return payload.decode()


@event_receiver()
def parser(reader: Reader, handler: Callable[[str], None]) -> Receiver:
    size = yield from read_size(reader)
    text = yield from read_text(reader, size)
    handler(text)


messages = Collector(parser)

assert messages.send(b'3:one3:t') == ['one']
assert messages.send(b'wo') == ['two']
```
