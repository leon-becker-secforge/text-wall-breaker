# Testing local MCP edits without a harness reload

The `text-wall-breaker` MCP server is a long-running process, connected once at session
start. Editing `src/text_wall_breaker/*.py` does **not** change its behavior — the process
already has the old module imported in memory, and Python does not hot-reload imports.
Calling `mcp__text-wall-breaker__ask`/`show` again after an edit still exercises the stale
code.

To check the *current* state of the code without restarting the MCP connection:

1. Run a **fresh subprocess** against the venv, not the running MCP server:
   ```bash
   /path/to/repo/.venv/bin/python -c "
   import sys; sys.path.insert(0, 'src')
   from text_wall_breaker.page import build_page, build_multi_page
   html = build_multi_page(questions=[...], submit_url='http://127.0.0.1:1/submit')
   open('/tmp/.../check.html', 'w').write(html)
   "
   ```
   A fresh interpreter always re-reads the `.py` files from disk, so this reflects the
   real current state even while the MCP server is stale. Open the generated file
   locally as a `file://` page.
2. For an actual interactive round trip (submit button, JS behavior, not just layout),
   run `ask.py`/`show.py`'s own entry points the same way, in a throwaway script, rather
   than going through the live MCP tool.
3. Only restart the MCP server (`/mcp` reconnect, or restart Claude Code) when the fix is
   confirmed and you want the *live* tool to reflect it going forward.

# Styling changes: prototype first

A new visual style (a new element's look, a layout choice, a color, a widget shape) is
shown as a local prototype for the maintainer to choose from, rather than shipped as a
guess. Build it as in "Testing local MCP edits". Every prototype must show both themes:
the real theme toggle from `page.py`, or two static renders (`data-theme="light"` and
`"dark"`).
