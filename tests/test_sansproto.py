from struct import Struct
from typing import Callable, List, Tuple

import pytest

from sansproto import Collector, DataCoro, IncompleteError, Reader, Receiver, receiver


def test_read() -> None:
    @receiver
    def proto(handler: Callable[[Tuple[bytes, bytes]], None]) -> Receiver:
        reader = Reader()
        while True:
            hdr = yield from reader.read(2)
            body = yield from reader.read(3)
            handler((hdr, body))

    p = Collector(proto)
    assert p.send(b'f') == []
    assert p.send(b'oozam') == [(b'fo', b'oza')]
    assert p.send(b'b') == []
    assert p.send(b'foo') == [(b'mb', b'foo')]


def test_read_truncates_consumed_buffer() -> None:
    @receiver
    def proto(handler: Callable[[bytes], None]) -> Receiver:
        reader = Reader(truncate_size=0)
        while True:
            handler((yield from reader.read(1)))

    p = Collector(proto)
    assert p.send(b'fo') == [b'f', b'o']


def test_incomplete_read_size() -> None:
    @receiver
    def proto(handler: Callable[[bytes], None]) -> Receiver:
        reader = Reader()
        while True:
            handler((yield from reader.read(3)))

    p = Collector(proto)
    assert p.send(b'fo') == []

    with pytest.raises(IncompleteError) as ei:
        p.send(b'')
    assert ei.value.partial == b'fo'


def test_read_struct() -> None:
    @receiver
    def proto(handler: Callable[[Tuple[bytes, int]], None]) -> Receiver:
        reader = Reader()
        while True:
            prefix = yield from reader.read(1)
            (value,) = yield from reader.read_struct(Struct('!H'))
            handler((prefix, value))

    p = Collector(proto)
    assert p.send(b'a\x01') == []
    assert p.send(b'\x02') == [(b'a', 258)]


def test_read_struct_truncates_consumed_buffer() -> None:
    @receiver
    def proto(handler: Callable[[int], None]) -> Receiver:
        reader = Reader(truncate_size=0)
        struct = Struct('B')
        while True:
            (value,) = yield from reader.read_struct(struct)
            handler(value)

    p = Collector(proto)
    assert p.send(b'\x01\x02') == [1, 2]


def test_incomplete_read_struct() -> None:
    @receiver
    def proto(handler: Callable[[int], None]) -> Receiver:
        reader = Reader()
        struct = Struct('!H')
        while True:
            (value,) = yield from reader.read_struct(struct)
            handler(value)

    p = Collector(proto)
    assert p.send(b'\x01') == []

    with pytest.raises(IncompleteError) as ei:
        p.send(b'')
    assert ei.value.partial == b'\x01'


def test_read_until_search_start() -> None:
    @receiver
    def proto(handler: Callable[[str], None]) -> Receiver:
        reader = Reader()
        while True:
            data = yield from reader.read_until(b'boo')
            handler(data.decode())

    p = Collector(proto)
    assert p.send(b'somebo') == []
    assert p.send(b'omooboo') == ['some', 'moo']
    p.send(b'')

    with pytest.raises(RuntimeError, match='EOF'):
        p.send(b'foo')


def test_read_until_truncates_consumed_buffer() -> None:
    @receiver
    def proto(handler: Callable[[str], None]) -> Receiver:
        reader = Reader(truncate_size=0)
        while True:
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
            [[], ['1'], [], ['2'], [], ['3'], [], ['4'], [], ['5'], [], ['6'], []],
        ),
        (
            '1:2:3:4:5:6:',
            2,
            False,
            False,
            [['1'], ['2'], ['3'], ['4'], ['5'], ['6'], []],
        ),
        ('1:2:3:4:5:6:', 3, False, False, [['1'], ['2', '3'], ['4'], ['5', '6'], []]),
        ('1:2:3:4:5:6:', 4, False, False, [['1', '2'], ['3', '4'], ['5', '6'], []]),
        ('1:2:3:4:5:6:', 5, False, False, [['1', '2'], ['3', '4', '5'], ['6'], []]),
        ('1:2:3:4:5:6:', 6, False, False, [['1', '2', '3'], ['4', '5', '6'], []]),
    ],
)
def test_read_until(
    stream: str, chunk_size: int, include: bool, eof: bool, expected: List[List[str]]
) -> None:
    @receiver
    def proto(handler: Callable[[str], None]) -> Receiver:
        reader = Reader()
        while True:
            data = yield from reader.read_until(b':', include=include, eof=eof)
            handler(data.decode())

    p = Collector(proto)
    data = stream.encode()
    result = []
    for start in range(0, len(data), chunk_size):
        result.append(p.send(data[start : start + chunk_size]))
    result.append(p.send(b''))
    assert result == expected


def test_incomplete_read() -> None:
    @receiver
    def proto(handler: Callable[[str], None]) -> Receiver:
        reader = Reader()
        while True:
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

    @receiver
    def proto(handler: Callable[[str], None]) -> Receiver:
        reader = Reader()
        while True:
            size = yield from parse_hdr(reader)
            data = yield from parse_body(reader, size)
            handler(data)

    p = Collector(proto)
    assert p.send(b'1:b2:fo') == ['b', 'fo']


def test_read_until_eof() -> None:
    @receiver
    def proto(handler: Callable[[str], None]) -> Receiver:
        reader = Reader()
        while True:
            line = yield from reader.read_until(b':', eof=True)
            handler(line.decode())

    p = Collector(proto)
    assert p.send(b'boo:foo') == ['boo']
    assert p.send(b'') == ['foo']

    with pytest.raises(RuntimeError, match='EOF'):
        p.send(b'')
