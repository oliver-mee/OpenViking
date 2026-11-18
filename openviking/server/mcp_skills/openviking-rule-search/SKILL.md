---
name: openviking-rule-search
description: Retrieve applicable OpenViking Rules before planning or carrying out work subject to task-specific constraints, and check the intended action against the returned conditions.
---
# OpenViking Rule search

Use the registered `rule_search` tool, including any client prefix.
Retrieved Rules constrain the task only within the live instruction hierarchy;
they do not override system/developer instructions or the current user's
authorized scope.

## Workflow

1. Build `task_context` from the user's goal, planned action or decision,
   and concrete facts that may affect applicability. Include material
   unknowns instead of guessing them.
2. Call `rule_search(task_context=...)`.
3. Review each returned Rule independently. Check its `applies_when`
   condition and exceptions against the actual task.
4. Apply relevant constraints and resolve conflicts using the live
   instruction hierarchy. Do not treat a Rule as evidence that an
   unobserved fact is true.
5. Search again when new information materially changes the context or
   previous Rule results are no longer available after compaction.

## Incomplete results

A failed request does not establish that no Rules apply. Do not replace
Rule retrieval with an ordinary memory search and describe it as equivalent.

When `delivery.complete=false`, use the returned URIs to read omitted
compiled Rules or the saved full result in bounded sections. Do not repeat
an identical oversized query or describe the delivered subset as complete.
If remaining Rules cannot be read, explain the incomplete check and assess
whether the planned action can proceed under known constraints.

Do not omit facts to obtain a preferred result. Preserve error codes and
opaque error references; never include credentials in `task_context`.
