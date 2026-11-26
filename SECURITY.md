# Security policy

text-wall-breaker is an MCP server that shows the user a question, a result or a
diff in a local browser page.

This policy says which versions receive security fixes and how to report a
vulnerability privately. It also lists the parts of the project that are in scope
and out of scope.

## Supported versions

Only the latest release receives security fixes. A fix ships as a new release;
older releases get no backports.

## Reporting a vulnerability

Report a vulnerability through GitHub's private advisory form: the "Report a
vulnerability" button on the repository's Security tab. Do not open a public issue
for it.

A report should name the affected version, the steps that reproduce the issue and
its effect. The project has a single maintainer, so replies are best effort, with
no fixed response time.

## Scope

The listener and the session token are described in
[How a call works](docs/how-a-call-works.md).

A report is in scope when it concerns one of these parts:

- the loopback listener and its check of the session token,
- the checks of the `Host` header that turn away DNS rebinding,
- the Content Security Policy of the generated pages,
- the rendering of untrusted Markdown, AsciiDoc and diffs, for example script
  injection into a page,
- the removal of the session token from the log.

A report is out of scope when it concerns one of these:

- an attacker who already runs code on the machine as the user who runs the
  server,
- the mermaid renderer that pages load from jsDelivr. The `--no-mermaid` option
  turns that loading off.
