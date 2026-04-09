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

Parsers in `sansproto` are generator coroutines. They receive byte chunks through
`send()`, suspend when they need more data, and emit parsed events through a callback.

`Reader` holds the buffered bytes and exposes helpers such as `read()`,
`read_struct()`, and `read_until()`. Your parser code describes protocol structure, and
`sansproto` handles split chunks, short reads, and EOF.

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

## Tutorial

### 1. Start with `receiver`

Use `receiver` when you want full control over the parsing loop. Create a
`Reader`, call `reader.begin_event()` before parsing one complete event, and emit parsed
values through a callback.

This parser reads a decimal byte length followed by `:`, then reads exactly that many
payload bytes.

```python
from typing import Callable

from sansproto import Parser, Reader, receiver


@receiver
def parser(emit: Callable[[str], None]) -> Parser:
    reader = Reader()
    while True:
        reader.begin_event()
        size = int((yield from reader.read_until(b':')))
        payload = yield from reader.read(size)
        emit(payload.decode())
```

The decorated parser is a stateful receiver object. Feed byte chunks into it until EOF
closes it.

```python
receiver = parser(print)
while receiver.open:
    receiver.send(sock.recv(65536))
```

### 2. Emit events through a callback

`sansproto` parsers do not return parsed values directly from `send()`. A chunk may
contain half an event, one event, or several events, so parsed values are pushed to a
callback instead.

You can provide any callback you want:

```python
messages = []
receiver = parser(messages.append)

assert receiver.open
receiver.send(b'5:he')
receiver.send(b'llo3:bye')
receiver.send(b'')
assert messages == ['hello', 'bye']
```

### 3. Use `Collector` when a list is enough

If you just want the events produced by each chunk, `Collector` provides the callback
for you and collects emitted values into a list.

```python
from typing import Callable

from sansproto import Collector, Parser, Reader, receiver


@receiver
def parser(emit: Callable[[str], None]) -> Parser:
    reader = Reader()
    while True:
        reader.begin_event()
        size = int((yield from reader.read_until(b':')))
        payload = yield from reader.read(size)
        emit(payload.decode())


messages = Collector(parser)

assert messages.send(b'5:he') == []
assert messages.send(b'llo3:') == ['hello']
assert messages.send(b'bye') == ['bye']
assert messages.send(b'') == []
assert not messages.open
```

If your receiver factory takes additional arguments after the emit, pass them to
`Collector` too.

```python
messages = Collector(parser_factory, arg1, arg2, option=True)
```

### 4. Compose parser helpers

Parser helpers can be generator functions too. Use `yield from` to delegate part of the
protocol to a smaller parser and return the parsed value to the caller.

```python
from typing import Callable

from sansproto import Collector, ReaderCoro, Parser, Reader, receiver


def read_size(reader: Reader) -> ReaderCoro[int]:
    raw_size = yield from reader.read_until(b':')
    return int(raw_size)


def read_text(reader: Reader, size: int) -> ReaderCoro[str]:
    payload = yield from reader.read(size)
    return payload.decode()


@receiver
def parser(emit: Callable[[str], None]) -> Parser:
    reader = Reader()
    while True:
        reader.begin_event()
        size = yield from read_size(reader)
        text = yield from read_text(reader, size)
        emit(text)


messages = Collector(parser)

assert messages.send(b'3:one3:t') == ['one']
assert messages.send(b'wo') == ['two']
```
