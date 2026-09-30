# `review` is a separate tool from `ask`, named for what it does

Both `ask` and `review` block waiting on the operator, so folding `review` into `ask` as
another parameter shape was considered. Rejected: `ask`'s question/option model doesn't
compose with line-anchored comments and free page navigation — the two dialogs solve
different shapes of input (a discrete choice vs. annotating an artifact). Keeping them
separate keeps `ask`'s existing semantics (wizard steps, all-or-nothing cancel)
unpolluted by `review`'s different ones (free navigation, partial cancel — see
[0002](./0002-review-cancel-returns-partial-comments.md)), and avoids an agent picking
the wrong parameter shape on an overloaded tool.

The same reasoning ruled out naming it (or any tool like it) `feedback`: a future
ordering/ranking or slider-input tool would need an unrelated data model (items and
positions/weights, not lines and paths), so `feedback` stays an informal umbrella term
for a family of tools, never the name of one. This tool is named `review` because that's
exactly and only what it does.
