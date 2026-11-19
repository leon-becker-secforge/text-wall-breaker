# Markdown subset

The Markdown subset is the set of Markdown constructs that text-wall-breaker renders
on its pages. This document lists what is supported, how images and links are
resolved, and why raw HTML is never passed through.

It does not cover the tools that take Markdown. Those are
[The `ask` tool](ask-tool.md), [The `show` tool](show-tool.md) and
[The `review` tool](review-tool.md).

A deliberate subset: headings, paragraphs, fenced and inline code, unordered and ordered
lists (nestable by indentation), blockquotes, GFM pipe tables (with `:--`/`:-:`/`--:`
alignment), links, images, bold, italic, and strikethrough. Anything outside it renders
as literal text.

A ` ```diff ` fenced block gets its own minimal rendering instead of a plain code block:
a colored left-edge rule and `+`/`-` markers per line, with removed lines struck through.

A ` ```mermaid ` fenced block renders as an actual diagram, centered under the
surrounding prose. This is the one deliberate exception to the page otherwise never
making a network call beyond its one POST: rather than vendor the mermaid library
(~3.5MB) into this package, a page with a mermaid fence loads a pinned build from
jsDelivr — never on a page without one. The diagram's theme is read once, from the
page's own resolved theme, at load; it does not follow the in-page toggle afterwards.
A diagram is drawn when it is first on screen, so one on a page or slide that starts
hidden is not drawn until it is shown.

The rendered view of a Markdown or AsciiDoc file in `review` draws diagrams the same way:
a ` ```mermaid ` fence in Markdown, and `[mermaid]` or `[source,mermaid]` on a listing in
AsciiDoc, the forms GitLab and GitHub draw. A comment on a diagram lands on the lines of
its fence. A review page without such a diagram still makes no network request.

Raw HTML is never passed through. Every run of source text is escaped before any tag is
added, so a `<script>` in a question appears as visible text. Rendering happens in Python
rather than in the page's JavaScript for that reason — the page is never handed a string
it has to trust.

Images use standard Markdown image syntax with an absolute path, as a `file://` URL or
bare (`/...` or `~/...`):

```markdown
![the current layout](file:///home/you/shot.png)
![the current layout](/home/you/shot.png)
```

A relative path renders as text: the server cannot know which directory it is relative to.
So does a remote (`http`/`https`) image: loading it would tell its server that the page was
opened, and from where.

There is no separate images field and no base64 inlining. The page is opened from disk,
so `file://` resolves directly. URLs are restricted to `http`, `https`, `file`, `mailto`
and `data:image/`; anything else, `javascript:` above all, renders as text.

Every page carries a Content-Security-Policy: only the page's own scripts run (each
carries a per-page nonce), mermaid loads only as the pinned file with its integrity hash,
images come from the page's own server, `file:` or `data:`, and requests go only to the
loopback listener.

An `ask` or `show` page widens itself automatically when its content carries a
` ```diff `/` ```code ` fence or a table — the same content shapes that would otherwise
hit their own horizontal scrollbar early, at the normal width. This is a structural
check, not a length one: a merely long paragraph stays at the normal width, since prose
gets harder to read wider, not easier. Two or more image-bearing options trigger the
same widened layout (see [The `ask` tool](ask-tool.md)). `review`'s own page always sizes itself to the
window instead, so it doesn't need this.
