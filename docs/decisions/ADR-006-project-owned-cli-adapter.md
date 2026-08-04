# ADR-006 — Project-owned command-line adapter

## Status

Accepted for MVP.

## Context

The system architecture originally named CLI11 as the command-line parser, but the repository did
not declare or pin that dependency. TASK-001 already established a small project-owned entry point,
and TASK-007 needs only two bounded subcommands with explicit long options. Adding another
third-party dependency would not remove the need for project-owned result envelopes, channel
routing, stable exit-code translation or semantic validation.

## Decision

Keep command parsing and presentation in a small project-owned C++20 adapter for the MVP. The
adapter owns argv parsing, help, human/JSON rendering, stderr logging and exit-code translation.
Input inspection, replay coordination and diagnostic publication remain independent domain modules
with typed inputs and results.

Unknown and duplicate options fail, option values are bounded before domain use, and tests exercise
the public adapter through injected output streams. No command parser is exposed across the
C++/Python file boundary.

## Alternatives considered

- CLI11: mature and concise, but adds a build dependency without replacing the project-specific
  contracts required by the current two-command surface.
- getopt: widely available on Unix-like systems but not a portable C++20 interface and awkward for
  the required subcommands.
- Parsing inside each domain component: couples trusted domain logic to argv and output streams.

## Consequences

Positive:

- The build dependency surface stays unchanged.
- Command-boundary behaviour remains directly testable and domain modules stay reusable.
- Runtime remains offline with no dependency download.

Negative:

- The project owns option-parsing edge cases and help text.
- The adapter may become repetitive as later tasks add commands and shared option groups.

## Conditions that justify revisiting

- Later command work produces material duplicated parsing or validation logic.
- Shell completion, nested subcommands or richer generated help becomes a concrete requirement.
- Measured maintenance cost exceeds the cost of pinning and licence-reviewing a dedicated parser.
