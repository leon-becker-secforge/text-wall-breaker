# Testing local MCP edits without a harness reload or an upload

The `text-wall-breaker` MCP server is a long-running process, connected once at session
start. Editing `src/text_wall_breaker/*.py` does **not** change its behavior — the process
already has the old module imported in memory, and Python does not hot-reload imports.
Calling `mcp__text-wall-breaker__ask`/`show` again after an edit still exercises the stale
code.

To check the *current* state of the code without restarting the MCP connection, and
without publishing anything externally (never upload local content to an outside service,
Artifact tool included, unless Operator asks for a shareable page):

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
   real current state even while the MCP server is stale.
2. Open the generated file **locally** — `xdg-open /tmp/.../check.html` — instead of
   publishing it anywhere. It's a local `file://` page; no upload needed to look at it.
3. For an actual interactive round trip (submit button, JS behavior, not just layout),
   run `ask.py`/`show.py`'s own entry points the same way, in a throwaway script, rather
   than going through the live MCP tool.
4. Only restart the MCP server (`/mcp` reconnect, or restart Claude Code) when the fix is
   confirmed and you want the *live* tool to reflect it going forward.

# Styling changes: use the /mattpocock-skills:prototype skill

A new visual style (a new element's look, a layout choice, a color, a widget shape) needs
the `/mattpocock-skills:prototype` skill: prototype it and let Operator choose, rather than
shipping a guess. Build the prototype locally as in "Testing local MCP edits" (no upload).
Every prototype must let Operator see both themes: the real theme toggle from `page.py`,
or two static renders (`data-theme="light"` and `"dark"`).
