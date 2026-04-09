from struct import Struct
from typing import Callable, List, Tuple

import pytest

from sansproto import (
    Collector,
    DataCoro,
    IncompleteError,
    Reader,
    Receiver,
    StreamClosedException,
    event_receiver,
    stream_receiver,
)


def test_read() -> None:
    @event_receiver()
    def proto(reader: Reader, handler: Callable[[Tuple[bytes, bytes]], None]) -> Receiver:
        hdr = yield from reader.read(2)
        body = yield from reader.read(3)
        handler((hdr, body))

    p = Collector(proto)
    assert p.send(b'f') == []
    assert p.send(b'oozam') == [(b'fo', b'oza')]
    assert p.send(b'b') == []
    assert p.send(b'foo') == [(b'mb', b'foo')]


def test_read_truncates_consumed_buffer() -> None:
    @event_receiver(truncate_size=0)
    def proto(reader: Reader, handler: Callable[[bytes], None]) -> Receiver:
        handler((yield from reader.read(1)))

    p = Collector(proto)
    assert p.send(b'fo') == [b'f', b'o']


def test_incomplete_read_size() -> None:
    @event_receiver()
    def proto(reader: Reader, handler: Callable[[bytes], None]) -> Receiver:
        handler((yield from reader.read(3)))

    p = Collector(proto)
    assert p.send(b'fo') == []

    with pytest.raises(IncompleteError) as ei:
        p.send(b'')
    assert ei.value.partial == b'fo'


def test_read_struct() -> None:
    @event_receiver()
    def proto(reader: Reader, handler: Callable[[Tuple[bytes, int]], None]) -> Receiver:
        prefix = yield from reader.read(1)
        (value,) = yield from reader.read_struct(Struct('!H'))
        handler((prefix, value))

    p = Collector(proto)
    assert p.send(b'a\x01') == []
    assert p.send(b'\x02') == [(b'a', 258)]


def test_read_struct_truncates_consumed_buffer() -> None:
    @event_receiver(truncate_size=0)
    def proto(reader: Reader, handler: Callable[[int], None]) -> Receiver:
        struct = Struct('B')
        (value,) = yield from reader.read_struct(struct)
        handler(value)

    p = Collector(proto)
    assert p.send(b'\x01\x02') == [1, 2]


def test_incomplete_read_struct() -> None:
    @event_receiver()
    def proto(reader: Reader, handler: Callable[[int], None]) -> Receiver:
        struct = Struct('!H')
        (value,) = yield from reader.read_struct(struct)
        handler(value)

    p = Collector(proto)
    assert p.send(b'\x01') == []

    with pytest.raises(IncompleteError) as ei:
        p.send(b'')
    assert ei.value.partial == b'\x01'


def test_read_until_search_start() -> None:
    @event_receiver()
    def proto(reader: Reader, handler: Callable[[str], None]) -> Receiver:
        data = yield from reader.read_until(b'boo')
        handler(data.decode())

    p = Collector(proto)
    assert p.send(b'somebo') == []
    assert p.send(b'omooboo') == ['some', 'moo']
    assert p.send(b'') == []
    assert not p.open


def test_read_until_truncates_consumed_buffer() -> None:
    @event_receiver(truncate_size=0)
    def proto(reader: Reader, handler: Callable[[str], None]) -> Receiver:
        data = yield from reader.read_until(b':')
        handler(data.decode())

    p = Collector(proto)
    assert p.send(b'a:b:') == ['a', 'b']


@pytest.mark.parametrize(
    'stream,chunk_size,include,eof,expected',
    [
        ('bo:f:', 1, False, False, [[], [], ['bo'], [], ['f'], []]),
        ('bo:f:', 1, True, False, [[], [], ['bo:'], [], ['f:'], []]),
        ('boo:f:', 3, False, False, [[], ['boo', 'f'], []]),
        ('boo', 3, False, True, [[], ['boo']]),
        ('boo', 3, True, True, [[], ['boo:']]),
        (
            '1:2:3:4:5:6:',
            1,
            False,
            False,
            [
                [],
                ['1'],
                [],
                ['2'],
                [],
                ['3'],
                [],
                ['4'],
                [],
                ['5'],
                [],
                ['6'],
                [],
            ],
        ),
        (
            '1:2:3:4:5:6:',
            2,
            False,
            False,
            [['1'], ['2'], ['3'], ['4'], ['5'], ['6'], []],
        ),
        (
            '1:2:3:4:5:6:',
            3,
            False,
            False,
            [['1'], ['2', '3'], ['4'], ['5', '6'], []],
        ),
        (
            '1:2:3:4:5:6:',
            4,
            False,
            False,
            [['1', '2'], ['3', '4'], ['5', '6'], []],
        ),
        (
            '1:2:3:4:5:6:',
            5,
            False,
            False,
            [['1', '2'], ['3', '4', '5'], ['6'], []],
        ),
        (
            '1:2:3:4:5:6:',
            6,
            False,
            False,
            [['1', '2', '3'], ['4', '5', '6'], []],
        ),
    ],
)
def test_read_until(
    stream: str, chunk_size: int, include: bool, eof: bool, expected: List[List[str]]
) -> None:
    @event_receiver()
    def proto(reader: Reader, handler: Callable[[str], None]) -> Receiver:
        data = yield from reader.read_until(b':', include=include, eof=eof)
        handler(data.decode())

    p = Collector(proto)
    data = stream.encode()
    result: List[List[str]] = []
    for start in range(0, len(data), chunk_size):
        result.append(p.send(data[start : start + chunk_size]))
    result.append(p.send(b''))
    assert result == expected
    assert not p.open


def test_incomplete_read() -> None:
    @event_receiver()
    def proto(reader: Reader, handler: Callable[[str], None]) -> Receiver:
        data = yield from reader.read_until(b':')
        handler(data.decode())

    p = Collector(proto)
    assert p.send(b'foo') == []

    with pytest.raises(IncompleteError) as ei:
        assert p.send(b'') == []
    assert ei.value.partial == b'foo'


def test_composition() -> None:
    def parse_hdr(reader: Reader) -> DataCoro[int]:
        hdr = yield from reader.read_until(b':')
        return int(hdr)

    def parse_body(reader: Reader, size: int) -> DataCoro[str]:
        body = yield from reader.read(size)
        return body.decode()

    @event_receiver()
    def proto(reader: Reader, handler: Callable[[str], None]) -> Receiver:
        size = yield from parse_hdr(reader)
        data = yield from parse_body(reader, size)
        handler(data)

    p = Collector(proto)
    assert p.send(b'1:b2:fo') == ['b', 'fo']


def test_stream_closed_with_size_header_body_message() -> None:
    @event_receiver()
    def proto(reader: Reader, handler: Callable[[str], None]) -> Receiver:
        size = int((yield from reader.read_until(b':')))
        body = yield from reader.read(size)
        handler(body.decode())

    p = Collector(proto)
    assert p.send(b'3:one') == ['one']
    assert p.send(b'3:t') == []
    assert p.send(b'wo') == ['two']

    assert p.send(b'') == []
    assert not p.open


def test_parser_can_handle_stream_closed() -> None:
    @stream_receiver
    def proto(handler: Callable[[str], None]) -> Receiver:
        reader = Reader()
        try:
            while True:
                reader.start_event()
                size = int((yield from reader.read_until(b':')))
                body = yield from reader.read(size)
                handler(body.decode())
        except StreamClosedException:
            handler('closed')

    p = Collector(proto)
    assert p.send(b'3:one') == ['one']
    assert p.send(b'') == ['closed']
    assert not p.open

    with pytest.raises(RuntimeError, match='EOF'):
        p.send(b'')


def test_stream_closed_inside_message_is_incomplete() -> None:
    @event_receiver()
    def proto(reader: Reader, handler: Callable[[str], None]) -> Receiver:
        size = int((yield from reader.read_until(b':')))
        body = yield from reader.read(size)
        handler(body.decode())

    p = Collector(proto)
    assert p.send(b'3:') == []

    with pytest.raises(IncompleteError) as ei:
        p.send(b'')
    assert ei.value.partial == b''


def test_read_until_eof() -> None:
    @event_receiver()
    def proto(reader: Reader, handler: Callable[[str], None]) -> Receiver:
        line = yield from reader.read_until(b':', eof=True)
        handler(line.decode())

    p = Collector(proto)
    assert p.send(b'boo:foo') == ['boo']
    assert p.send(b'') == ['foo']
    assert not p.open


def test_stream_receiver_open_state() -> None:
    @stream_receiver
    def proto(handler: Callable[[str], None]) -> Receiver:
        reader = Reader()
        while True:
            reader.start_event()
            handler((yield from reader.read_until(b':')).decode())

    result: List[str] = []
    receiver = proto(result.append)
    assert receiver.open
    receiver.send(b'one:')
    assert result == ['one']
    assert receiver.open
    receiver.send(b'')
    assert not receiver.open


def test_handle_eof_after_eof_raises_runtime_error() -> None:
    reader = Reader()
    reader.eof = True

    with pytest.raises(RuntimeError, match='EOF'):
        reader.handle_eof()
