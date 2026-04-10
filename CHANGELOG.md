## 0.10

### Changed

- Parser functions now require an `emit` callback as their first argument.
  `@receiver` and `Collector` both expect parser factories with `emit` first,
  followed by any custom arguments.
- `@receiver` now returns a `Receiver` object instead of a bare send callable.
  The receiver exposes `.send(...)` and `.open`.
- `Collector` now exposes `.open` and supports passing custom parser arguments.
- End-of-stream handling is explicit. Clean EOF at an event boundary raises
  `StreamClosedException`, while EOF in the middle of an event raises
  `IncompleteError`.
- Parsers should call `Reader.begin_event()` before reading each event so EOF
  handling can distinguish clean stream closure from incomplete input.
- `Reader.read_until()` now uses `allow_partial=` instead of `eof=` for
  returning the final unterminated fragment at EOF.
- `Reader.handle_eof()` replaces the old `check_buf_is_empty()` helper.
- Exported type names were cleaned up:
  `Data` -> `Chunk`, `DataCoro` -> `ReaderCoro`, `DataCall` -> `Emitter`.
  A new `Parser` type alias is exported, and `Receiver` now refers to the
  concrete receiver wrapper class.
- Input chunk typing now accepts `memoryview` in addition to `bytes` and
  `bytearray`.

### Documentation

- The documentation was expanded with a new quickstart, detailed EOF behavior,
  `.open` usage, collector usage, event boundary guidance, parser composition,
  API reference material, and a link to general Sans-I/O documentation.

## 0.9

Initial release
