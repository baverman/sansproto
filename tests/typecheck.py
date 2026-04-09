from typing import Callable

from sansproto import Collector, Parser, Reader, receiver


def check_emit_arg() -> None:
    @receiver
    def parser(emit: Callable[[bytes], None]) -> Parser:
        reader = Reader()
        while True:
            emit((yield from reader.read(2)))


def check_custom_arg() -> None:
    @receiver
    def parser(emit: Callable[[bytes], None], size: int) -> Parser:
        reader = Reader()
        while True:
            emit((yield from reader.read(size)))


def check_collector_custom_arg() -> None:
    @receiver
    def parser(emit: Callable[[bytes], None], size: int) -> Parser:
        reader = Reader()
        while True:
            emit((yield from reader.read(size)))

    Collector(parser, 2)
    Collector(parser, 'aa')  # type: ignore[arg-type]
