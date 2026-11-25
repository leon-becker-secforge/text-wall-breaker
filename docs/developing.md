# Developing

text-wall-breaker is an MCP server that asks the user a question, shows them a result,
or has them review a diff, in a browser page. This document is for the people who work
on it. It gives the command that runs the tests and the checks to make before the
repository goes public.

From the repository root:

```bash
uv run pytest
```

The tests drive the listener over real loopback HTTP and substitute a fake browser that
reads the generated page and posts back, so the port/token handshake and the page's own
markup are covered without a display.

## Going public

Before the repository is made public:

- Scan the full history again: `gitleaks git .`.
- Read `CLAUDE.md`, `docs/adr/` and `CONTEXT.md` for internal names and paths.
- Check that the README's Options table still says `mermaid` diagrams load from jsDelivr
  and that `--no-mermaid` turns that off. That settles whether the CDN is acceptable for
  outside users: they can switch it off.
