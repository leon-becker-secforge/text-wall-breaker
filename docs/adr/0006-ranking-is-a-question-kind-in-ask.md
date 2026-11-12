# Ranking is a question kind in `ask`, not a tool of its own

[ADR 0001](./0001-review-is-a-separate-tool-from-ask.md) expected a ranking input to
become a separate tool, because its data model (items and positions) differs from a
choice. A ranking question is now an entry of `ask`'s `questions` with `rank` set. The
question's `options` are the items to rank, so ranking adds one field and no new item
shape.

The deciding need is a round that mixes kinds: "pick an approach, then rank the
follow-ups" is one page and one round trip. A separate tool needs a second tab and a
second call for that. Inside `ask`, a ranking question also gets the wizard, cancel,
Stop here, export and `resume` without new code for each.

`rank` takes two forms, picked in a prototype (branch `lab_prototype-ask-inputs`):

- `true`: one ordered list. The answer carries `order`, every option id once.
- A list of tier names, such as `["Now", "Next", "Later"]`: the options are sorted
  into those tiers. The answer carries `tiers`, a map of tier name to option ids, and
  `unsorted`.

Example:

```json
{"question": "Sort the backlog.", "options": [...], "rank": ["Now", "Later"]}
```

In this example, `Now` and `Later` are the tiers, and an option the user leaves alone
is returned in `unsorted`.

Consequences:

- `rank` exists only on `questions` entries. The single-question top level of `ask`
  does not take it; a lone ranking question is a `questions` list of length one.
- A ranking answer has no `selected` and no `changed_from`. An untouched ordered list
  is a valid answer, the agent's own order. A tiers question needs at least one option
  sorted into a tier, or free text.
- `multi_select`, `selected` and `recommended` are rejected on a ranking question,
  since they have no meaning there.
- A live discussion cannot be started from a ranking item: the thought bubble sits on
  choice options only.

Rejected:

- **A separate `rank` tool**, as ADR 0001 expected. It keeps `ask`'s schema smaller,
  but a mixed round costs two tabs and two calls.
- **Click to rank a top N**, the prototype's third variant. Tiers cover a partial
  ranking with less to explain.
- **An explicit `mode` field.** The form of `rank` already says which widget to show,
  and a second field could contradict it.
