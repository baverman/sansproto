# sansproto

[Sans-I/O][sans-io] helpers for byte stream protocol parsers.

[sans-io]: https://sans-io.readthedocs.io/

Byte stream protocols need to handle messages split across arbitrary chunks. One
`recv()` call can contain half a message, exactly one message, or several messages at
once. When buffering and parsing are mixed together, protocol code has to keep track of
partial input as well as message structure.

`sansproto` separates byte buffering from protocol parsing. That lets parser code
focus on message structure instead of keeping track of partial input by hand.

## Contents

- [Installation](#installation)
- [Quickstart](#quickstart)
- [How It Works](#how-it-works)
- [End Of Stream](#end-of-stream)
- [Integration With External Data Sources](#integration-with-external-data-sources)
- [Collecting Events](#collecting-events)
- [Event Boundaries](#event-boundaries)
- [Composing Parsers](#composing-parsers)
- [Reader Methods Reference](#reader-methods-reference)

## Installation

```bash
pip install sansproto
```

## Quickstart

This parser reads messages encoded as `<length>:<payload>`, such as
`5:hello` or `5:world`.

A parser function uses `Reader` to consume buffered input and calls `emit(...)`
to produce parsed events. The caller provides that callback when creating a
`Receiver`, and `@receiver` turns the parser into the corresponding factory.

```python
from typing import Callable

from sansproto import Parser, Reader, receiver


# `@receiver` turns this parser function into a Receiver factory.
# `emit` is a callback provided by the caller. The parser uses it to produce parsed messages.
@receiver
def parser(emit: Callable[[str], None]) -> Parser:
    reader = Reader()

    while True:
        # Mark the boundary of the next message.
        reader.begin_event()

        # Read the length prefix up to `:`.
        size = yield from reader.read_until(b':')

        # Read exactly that many payload bytes.
        payload = yield from reader.read(int(size))

        # Emit one parsed message.
        emit(payload.decode())


# Calling the decorated function creates a Receiver. `print` is used here as
# the callback that receives each parsed message.
protocol = parser(print)

# In practice, these chunks usually come from a socket or another byte source.
# The source can be synchronous or asynchronous; `sansproto` only consumes bytes.
protocol.send(b'5:hello')
protocol.send(b'5:world')

# prints: hello
# prints: world
```

## How It Works

The parser function runs as a generator.

When you call the decorated function, it creates the parser generator, advances
it to its first `yield`, and wraps it in a `Receiver`. The parser function does
not run from the beginning for each chunk. Instead, it pauses and resumes, so
its local variables stay alive between calls to `send()`. That is why `reader`
can be created inside the parser function and still keep its state across the
whole stream.

Each call to `Receiver.send(data)` resumes the parser and gives it one new chunk
of bytes. Inside the parser, expressions like `yield from reader.read(...)` and
`yield from reader.read_until(...)` suspend parsing until enough input has been
buffered. When more data is needed, control returns to `Receiver.send()`. The
next call to `send()` resumes the parser at the same point with another chunk.

`Reader` is the object that stores unread bytes between those resumptions. The
parser keeps one `Reader` instance in a local variable, and that reader keeps
accumulating input until there is enough data to return the next parsed piece.

In the quickstart example, this is what happens when you call
`protocol = parser(print)`:

1. Calling `parser(print)` creates the parser generator and runs it until it
   first reaches `size = yield from reader.read_until(b':')`.
2. `reader.read_until(b':')` does not have any buffered input yet, so it yields
   control and waits for the first chunk. At this point, `reader` already exists
   and is stored in the suspended parser state.

When you then call `protocol.send(b'5:hello')`:

3. That chunk is sent into the suspended `reader.read_until(b':')` call.
4. `Reader` buffers the chunk, finds `:`, and returns `b'5'`.
5. The parser continues, computes `int(size)`, and reaches
   `payload = yield from reader.read(int(size))`.
6. The payload bytes are already buffered, so `reader.read(...)` returns
   `b'hello'` immediately.
7. The parser calls `emit(payload.decode())`, so `print('hello')` runs.
8. The parser loops, calls `reader.begin_event()`, and reaches the next
   `yield from reader.read_until(b':')`, where it pauses again waiting for more input.

If the input arrives in smaller chunks, the same parser simply pauses earlier.
For example:

1. `protocol.send(b'5:he')` lets `reader.read_until(b':')` return `b'5'`, then
   enters `reader.read(5)`.
2. Only `b'he'` of the payload is buffered, so `reader.read(5)` yields and the
   parser pauses.
3. `protocol.send(b'llo')` resumes that same `reader.read(5)` call.
4. The remaining bytes arrive, `reader.read(5)` returns `b'hello'`, and the
   parser emits the decoded message.

## End Of Stream

Consider a parser that reads one line at a time:

```python
from typing import Callable

from sansproto import Parser, Reader, receiver


@receiver
def line_parser(emit: Callable[[str], None]) -> Parser:
    reader = Reader()
    while True:
        reader.begin_event()
        line = yield from reader.read_until(b'\n', allow_partial=True)
        emit(line.decode())
```

Complete lines work as expected:

```python
protocol = line_parser(print)
protocol.send(b'hello\n')
protocol.send(b'world\n')

# prints: hello
# prints: world
```

In line-oriented formats, the final line of the stream may not end with `\n`, so the
parser still needs a way to emit it.

Continuing the same stream, the next chunk does not end with `\n`, and no more
data will arrive after it:

```python
protocol.send(b'my name is Joe')

# nothing is printed yet
```

At this point the parser has seen `b'my name is Joe'`, but it cannot tell whether that is
the final line or just the start of a longer one.

The parser needs one more signal: that no more bytes are coming. In
`sansproto`, that signal is an empty chunk, `b''`:

```python
protocol = line_parser(print)
protocol.send(b'hello\n')
protocol.send(b'world\n')
protocol.send(b'my name is Joe')
protocol.send(b'')

# prints: hello
# prints: world
# prints: my name is Joe
```

That final `b''` does two things: it lets the parser finish the last line, and
it tells the receiver that no more input is coming.

You can observe that through the `open` attribute:

```python
protocol = line_parser(print)
assert protocol.open

protocol.send(b'hello')
protocol.send(b'')

assert not protocol.open
```

## Integration With External Data Sources

`sansproto` does not perform I/O itself. Your code reads chunks from an external
byte source and passes them into the receiver.

```python
protocol = parser(print)

# `sock` is a connected socket.
while protocol.open:
    chunk = sock.recv(65536)
    protocol.send(chunk)
```

This loop works because `socket.recv()` reports EOF as `b''`. The loop passes
every returned chunk straight into `send()`, including that final empty chunk.

`open` stays `True` while the receiver is still accepting input. Once EOF has
been passed into the parser and handled, the receiver closes and `open` becomes
`False`.

The same pattern works with any synchronous or asynchronous source that also
represents EOF as `b''`. If your data source reports EOF in some other way,
detect that condition in your own code and call `send(b'')` explicitly.

## Collecting Events

Passing a callback is useful when parsed events should be handled somewhere
else. If you want to process events in the same place where you feed input, use
`Collector`.

`Collector` wraps a parser and returns the events emitted during each `send()`
call as a list:

```python
from sansproto import Collector

collector = Collector(parser)

assert collector.send(b'5:he') == []
assert collector.send(b'llo5:world') == ['hello', 'world']
```

This can be more convenient when the code that reads input also wants to handle
the parsed events immediately:

```python
collector = Collector(parser)

# `sock` is a connected socket.
while collector.open:
    chunk = sock.recv(65536)
    for event in collector.send(chunk):
        handle_event(event)
```

`Collector` does not change how parsing works. It only changes how emitted
events are delivered: instead of calling your callback directly, it collects
them and returns them from `send()`.

## Event Boundaries

Every parser example calls `reader.begin_event()` before it starts reading the
next event:

```python
@receiver
def parser(emit: Callable[[str], None]) -> Parser:
    reader = Reader()
    while True:
        reader.begin_event()
        size = yield from reader.read_until(b':')
        payload = yield from reader.read(int(size))
        emit(payload.decode())
```

`begin_event()` marks the boundary where the next event starts. `Reader` uses
that boundary when EOF arrives.

If EOF happens exactly between events, the receiver closes cleanly:

```python
collector = Collector(parser)

assert collector.send(b'5:hello') == ['hello']
assert collector.send(b'') == []
assert not collector.open
```

If EOF happens after a new event has started but before it is complete,
`Reader` raises `IncompleteError`:

```python
from sansproto import Collector, IncompleteError

collector = Collector(parser)

assert collector.send(b'5:he') == []

try:
    collector.send(b'')
except IncompleteError as exc:
    assert exc.partial == b'he'
```

Here, `exc.partial` is the buffered tail of the unfinished event at the moment
EOF was received.

## Composing Parsers

As parsers grow, it is often useful to move parts of the protocol into helper
coroutines.

A helper takes a `Reader`, reads one piece of the input, and returns the parsed
value to its caller:

```python
from typing import Callable

from sansproto import Collector, Parser, Reader, ReaderCoro, receiver


def read_size(reader: Reader) -> ReaderCoro[int]:
    raw = yield from reader.read_until(b':')
    return int(raw)


def read_text(reader: Reader, size: int) -> ReaderCoro[str]:
    payload = yield from reader.read(int(size))
    return payload.decode()


@receiver
def parser(emit: Callable[[str], None]) -> Parser:
    reader = Reader()
    while True:
        reader.begin_event()
        size = yield from read_size(reader)
        text = yield from read_text(reader, size)
        emit(text)
```

The top-level parser still decides where each event starts and when to call
`emit(...)`, but the details of parsing each field can live in separate helper
coroutines.

```python
collector = Collector(parser)

assert collector.send(b'5:hello') == ['hello']
```

## Reader Methods Reference

These methods are the main building blocks used inside parser coroutines and
are meant to be used with `yield from`.

### Reader.read

`Reader.read(size: int) -> bytes`

Read exactly `size` bytes.

`read()` returns only after `size` bytes have been buffered, even if they
arrive across multiple chunks.

```python
payload = yield from reader.read(size)
```

### Reader.read_until

`Reader.read_until(separator: bytes, include: bool = False, allow_partial: bool = False) -> bytes`

Read up to the next `separator`.

`read_until()` returns the bytes before `separator`. The separator is always
consumed from the input, even when it is not included in the returned value.

If `include=True`, the returned value includes the separator.

If `allow_partial=True` and EOF arrives before `separator` is found,
`read_until()` returns the unread buffered tail instead of raising
`IncompleteError`.

```python
line = yield from reader.read_until(b'\n')
header = yield from reader.read_until(b':', include=True)
tail = yield from reader.read_until(b'\n', allow_partial=True)
```

### Reader.read_struct

`Reader.read_struct(struct: struct.Struct) -> Tuple[Any, ...]`

Read bytes and unpack them with a `struct.Struct`.

`read_struct()` waits until enough bytes are buffered for the given format, then
unpacks them and consumes them from the input.

```python
size_struct = struct.Struct('!H')
(size,) = yield from reader.read_struct(size_struct)
```
