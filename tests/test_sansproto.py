from struct import Struct
from typing import Callable, List, Tuple

import pytest

from sansproto import (
    Collector,
    IncompleteError,
    Parser,
    Reader,
    ReaderCoro,
    StreamClosedException,
    receiver,
)


def test_read() -> None:
    @receiver
    def proto(emit: Callable[[Tuple[bytes, bytes]], None]) -> Parser:
        reader = Reader()
        while True:
            reader.begin_event()
            hdr = yield from reader.read(2)
            body = yield from reader.read(3)
            emit((hdr, body))

    p = Collector(proto)
    assert p.send(b'f') == []
    assert p.send(b'oozam') == [(b'fo', b'oza')]
    assert p.send(b'b') == []
    assert p.send(b'foo') == [(b'mb', b'foo')]


def test_read_truncates_consumed_buffer() -> None:
    @receiver
    def proto(emit: Callable[[bytes], None]) -> Parser:
        reader = Reader(truncate_size=0)
        while True:
            reader.begin_event()
            emit((yield from reader.read(1)))

    p = Collector(proto)
    assert p.send(b'fo') == [b'f', b'o']


def test_incomplete_read_size() -> None:
    @receiver
    def proto(emit: Callable[[bytes], None]) -> Parser:
        reader = Reader()
        while True:
            reader.begin_event()
            emit((yield from reader.read(3)))

    p = Collector(proto)
    assert p.send(b'fo') == []

    with pytest.raises(IncompleteError) as ei:
        p.send(b'')
    assert ei.value.partial == b'fo'


def test_read_struct() -> None:
    @receiver
    def proto(emit: Callable[[Tuple[bytes, int]], None]) -> Parser:
        reader = Reader()
        while True:
            reader.begin_event()
            prefix = yield from reader.read(1)
            (value,) = yield from reader.read_struct(Struct('!H'))
            emit((prefix, value))

    p = Collector(proto)
    assert p.send(b'a\x01') == []
    assert p.send(b'\x02') == [(b'a', 258)]


def test_read_struct_truncates_consumed_buffer() -> None:
    @receiver
    def proto(emit: Callable[[int], None]) -> Parser:
        reader = Reader(truncate_size=0)
        struct = Struct('B')
        while True:
            reader.begin_event()
            (value,) = yield from reader.read_struct(struct)
            emit(value)

    p = Collector(proto)
    assert p.send(b'\x01\x02') == [1, 2]


def test_incomplete_read_struct() -> None:
    @receiver
    def proto(emit: Callable[[int], None]) -> Parser:
        reader = Reader()
        struct = Struct('!H')
        while True:
            reader.begin_event()
            (value,) = yield from reader.read_struct(struct)
            emit(value)

    p = Collector(proto)
    assert p.send(b'\x01') == []

    with pytest.raises(IncompleteError) as ei:
        p.send(b'')
    assert ei.value.partial == b'\x01'


def test_read_until_search_start() -> None:
    @receiver
    def proto(emit: Callable[[str], None]) -> Parser:
        reader = Reader()
        while True:
            reader.begin_event()
            data = yield from reader.read_until(b'boo')
            emit(data.decode())

    p = Collector(proto)
    assert p.send(b'somebo') == []
    assert p.send(b'omooboo') == ['some', 'moo']
    assert p.send(b'') == []
    assert not p.open


def test_read_until_truncates_consumed_buffer() -> None:
    @receiver
    def proto(emit: Callable[[str], None]) -> Parser:
        reader = Reader(truncate_size=0)
        while True:
            reader.begin_event()
            data = yield from reader.read_until(b':')
            emit(data.decode())

    p = Collector(proto)
    assert p.send(b'a:b:') == ['a', 'b']


@pytest.mark.parametrize(
    'stream,chunk_size,include,allow_partial,expected',
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
    stream: str,
    chunk_size: int,
    include: bool,
    allow_partial: bool,
    expected: List[List[str]],
) -> None:
    @receiver
    def proto(emit: Callable[[str], None]) -> Parser:
        reader = Reader()
        while True:
            reader.begin_event()
            data = yield from reader.read_until(b':', include=include, allow_partial=allow_partial)
            emit(data.decode())

    p = Collector(proto)
    data = stream.encode()
    result: List[List[str]] = []
    for start in range(0, len(data), chunk_size):
        result.append(p.send(data[start : start + chunk_size]))
    result.append(p.send(b''))
    assert result == expected
    assert not p.open


def test_incomplete_read() -> None:
    @receiver
    def proto(emit: Callable[[str], None]) -> Parser:
        reader = Reader()
        while True:
            reader.begin_event()
            data = yield from reader.read_until(b':')
            emit(data.decode())

    p = Collector(proto)
    assert p.send(b'foo') == []

    with pytest.raises(IncompleteError) as ei:
        assert p.send(b'') == []
    assert ei.value.partial == b'foo'


def test_composition() -> None:
    def parse_hdr(reader: Reader) -> ReaderCoro[int]:
        hdr = yield from reader.read_until(b':')
        return int(hdr)

    def parse_body(reader: Reader, size: int) -> ReaderCoro[str]:
        body = yield from reader.read(size)
        return body.decode()

    @receiver
    def proto(emit: Callable[[str], None]) -> Parser:
        reader = Reader()
        while True:
            reader.begin_event()
            size = yield from parse_hdr(reader)
            data = yield from parse_body(reader, size)
            emit(data)

    p = Collector(proto)
    assert p.send(b'1:b2:fo') == ['b', 'fo']


def test_stream_closed_with_size_header_body_message() -> None:
    @receiver
    def proto(emit: Callable[[str], None]) -> Parser:
        reader = Reader()
        while True:
            reader.begin_event()
            size = int((yield from reader.read_until(b':')))
            body = yield from reader.read(size)
            emit(body.decode())

    p = Collector(proto)
    assert p.send(b'3:one') == ['one']
    assert p.send(b'3:t') == []
    assert p.send(b'wo') == ['two']

    assert p.send(b'') == []
    assert not p.open


def test_parser_can_handle_stream_closed() -> None:
    @receiver
    def proto(emit: Callable[[str], None]) -> Parser:
        reader = Reader()
        try:
            while True:
                reader.begin_event()
                size = int((yield from reader.read_until(b':')))
                body = yield from reader.read(size)
                emit(body.decode())
        except StreamClosedException:
            emit('closed')

    p = Collector(proto)
    assert p.send(b'3:one') == ['one']
    assert p.send(b'') == ['closed']
    assert not p.open

    with pytest.raises(RuntimeError, match='closed receiver'):
        p.send(b'')


def test_stream_closed_inside_message_is_incomplete() -> None:
    @receiver
    def proto(emit: Callable[[str], None]) -> Parser:
        reader = Reader()
        while True:
            reader.begin_event()
            size = int((yield from reader.read_until(b':')))
            body = yield from reader.read(size)
            emit(body.decode())

    p = Collector(proto)
    assert p.send(b'3:') == []

    with pytest.raises(IncompleteError) as ei:
        p.send(b'')
    assert ei.value.partial == b''


def test_read_until_eof() -> None:
    @receiver
    def proto(emit: Callable[[str], None]) -> Parser:
        reader = Reader()
        while True:
            reader.begin_event()
            line = yield from reader.read_until(b':', allow_partial=True)
            emit(line.decode())

    p = Collector(proto)
    assert p.send(b'boo:foo') == ['boo']
    assert p.send(b'') == ['foo']
    assert not p.open


def test_receiver_open_state() -> None:
    @receiver
    def proto(emit: Callable[[str], None]) -> Parser:
        reader = Reader()
        while True:
            reader.begin_event()
            emit((yield from reader.read_until(b':')).decode())

    result: List[str] = []
    data_receiver = proto(result.append)
    assert data_receiver.open
    data_receiver.send(b'one:')
    assert result == ['one']
    assert data_receiver.open
    data_receiver.send(b'')
    assert not data_receiver.open


def test_handle_eof_after_eof_raises_runtime_error() -> None:
    reader = Reader()
    reader._eof = True

    with pytest.raises(RuntimeError, match='closed receiver'):
        reader.handle_eof()
