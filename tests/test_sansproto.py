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
