# A comment range carries a side on each end

A `review` comment may cover several rows, and in a changed block the deleted rows and
the added rows have different line numbers. A range therefore keeps `side`/`line` as its
start and adds `end_side`/`end_line` for its end, each end naming its own side, so a
replaced block (`old 8 → new 9`) is expressible exactly. `end_side`/`end_line` appear
only on a range, and a partial text selection adds `quote`; a single-line comment is
byte-for-byte the shape 0.9.0 returned.

Rejected:

- **`start: {side, line}` / `end: {side, line}` on every comment.** One uniform shape,
  but it breaks every consumer of `side`/`line`, the contract since 0.9.0, for a feature
  most comments don't use.
- **One side per range.** Keeps the shape flat, but a comment on a replaced block becomes
  two comments or silently loses the rows of the other side.

`context` of a range is every row of it, one per line, each prefixed with its diff marker
(`+`, `-`, space), because a mixed range is ambiguous without them. `context` of a
single-line comment stays the plain line text.
