from typing import Callable

from sansproto import Collector, Reader, Receiver, event_receiver, stream_receiver


def check_handler_arg() -> None:
    # Both stream and event receiver should accept handlerless parsers

    @event_receiver()
    def e_parser(reader: Reader) -> Receiver:
        _ = yield from reader.read(2)

    # Now only stream receiver allows it
    @stream_receiver
    def s_parser() -> Receiver:
        reader = Reader()
        while True:
            _ = yield from reader.read(2)


def check_custom_arg() -> None:
    # Both stream and event receiver should accept any additional arguments

    @event_receiver()
    def e_parser(reader: Reader, size: int) -> Receiver:
        _ = yield from reader.read(size)

    @stream_receiver
    def s_parser(size: int) -> Receiver:
        reader = Reader()
        while True:
            _ = yield from reader.read(size)


def check_collector_custom_arg() -> None:
    @event_receiver()
    def e_parser(reader: Reader, handler: Callable[[bytes], None], size: int) -> Receiver:
        handler((yield from reader.read(size)))

    @stream_receiver
    def s_parser(handler: Callable[[bytes], None], size: int) -> Receiver:
        reader = Reader()
        while True:
            handler((yield from reader.read(size)))

    Collector(e_parser, 2)
    Collector(e_parser, 'aa')  # type: ignore[arg-type]

    Collector(s_parser, 2)
    Collector(s_parser, 'aa')  # type: ignore[arg-type]
