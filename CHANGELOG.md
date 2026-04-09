## 0.10dev

### Changed

- Explicit control over stream end condition. Receivers could handle
  `StreamClosedException` and decide that to do.
- Receivers and collectors now expose `.open`.
- `event_receiver` decorator to aid simple parser implementations.

## 0.9

Initial release
