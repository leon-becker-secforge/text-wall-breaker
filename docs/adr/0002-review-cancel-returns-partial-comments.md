# `review` cancel returns partial comments, unlike `ask`

`ask` treats cancel as all-or-nothing: no partial answer is ever returned. `review`
deliberately diverges — cancelling returns whatever comments and overall note were
already left (`{"cancelled": true, "comments": [...], "overall_note": ...}`). An operator
reviewing several files may cancel on purpose after finishing a partial pass, intending
the agent to act on what's flagged so far; discarding that work would undermine the
tool's own purpose. This asymmetry with `ask` is intentional and should not be "fixed"
to match it.
