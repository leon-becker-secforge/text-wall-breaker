# Testing local MCP edits without a harness reload or an upload

The `pretty-prompt` MCP server is a long-running process, connected once at session
start. Editing `src/pretty_prompt/*.py` does **not** change its behavior — the process
already has the old module imported in memory, and Python does not hot-reload imports.
Calling `mcp__pretty-prompt__ask`/`show` again after an edit still exercises the stale
code.

To check the *current* state of the code without restarting the MCP connection, and
without publishing anything externally (no Artifact tool — see memory
`feedback-no-artifact-uploads`):

1. Run a **fresh subprocess** against the venv, not the running MCP server:
   ```bash
   /path/to/repo/.venv/bin/python -c "
   import sys; sys.path.insert(0, 'src')
   from pretty_prompt.page import build_page, build_multi_page
   html = build_multi_page(questions=[...], submit_url='http://127.0.0.1:1/submit')
   open('/tmp/.../check.html', 'w').write(html)
   "
   ```
   A fresh interpreter always re-reads the `.py` files from disk, so this reflects the
   real current state even while the MCP server is stale.
2. Open the generated file **locally** — `xdg-open /tmp/.../check.html` — instead of
   publishing it anywhere. It's a local `file://` page; no upload needed to look at it.
3. For an actual interactive round trip (submit button, JS behavior, not just layout),
   run `ask.py`/`show.py`'s own entry points the same way, in a throwaway script, rather
   than going through the live MCP tool.
4. Only restart the MCP server (`/mcp` reconnect, or restart Claude Code) when the fix is
   confirmed and you want the *live* tool to reflect it going forward.

# Styling changes: always show three variants, never guess

When extending a rendered template (`page.py`'s CSS/markup, `markdown.py`'s output, or
`base.css`) with a new visual style — a new element's look, a layout choice, a color, a
widget shape — do not just pick one and ship it. Prepare **three variants**, render them
**side by side in one mock-up page** (built the same local, non-uploaded way as the
"testing local MCP edits" section above), open it, and let Operator choose. Refine from
there rather than guessing what looks good.

This applies to styling/visual decisions specifically — not to markdown syntax choices
(e.g. what an inline construct's grammar looks like), which are a separate kind of
decision and can be discussed as plain text/options instead of a rendered mock-up.

When several new visual objects are added in one piece of work (e.g. a badge, a card
frame, a nav button, all added for the same feature), review them **one at a time, each
in isolation** — its own three-variant mock-up, its own choice — not bundled into one
combined mock-up. A combined view hides how each object looks on its own and makes it
easy to wave through one bad choice riding alongside three good ones.

Every mock-up must let Operator see both themes, not just whichever one the OS happens
to be in when the page opens. Preferred: include the real theme-toggle button/script
(same one `page.py` already ships) so they can flip it live. Only fall back to two
static side-by-side renders (one `data-theme="light"`, one `data-theme="dark"`) if a
live toggle genuinely isn't practical for that particular mock-up.

