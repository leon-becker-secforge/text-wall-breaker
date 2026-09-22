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
   html = build_multi_page(questions=[...], submit_url='http://127.0.0.1:1/submit', timeout_seconds=300)
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
