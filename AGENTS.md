# Question Tracker — Codex Instructions

## Project

Question Tracker is a Windows desktop application for organising
university problem sheets, solutions and past papers, with tools
to make marking easier.

## Development principles

- Inspect the existing code before making changes.
- Reuse existing code and components wherever possible.
- Make the smallest change that solves the requested problem.
- Do not rewrite working code unnecessarily.
- Do not introduce dependencies unless they are necessary.
- Keep the application architecture simple and maintainable.
- Keep UI, application logic and data/storage logic separated.
- Do not make major architectural changes without discussing them first.

## Working with existing functionality

- Preserve existing behaviour unless a requested change requires otherwise.
- Do not modify unrelated parts of the application.
- Do not remove functionality simply to make an implementation easier.
- Before creating a new component, function or system, check whether an
  existing implementation can be reused.

## Testing

After implementing a change:

1. Run the relevant tests or checks.
2. Fix failures caused by the change.
3. Confirm that existing functionality still works.
4. Do not make speculative changes after the requested task is working.

## Debugging

When something fails:

- Identify the actual cause before changing code.
- Make the smallest appropriate fix.
- Do not repeatedly rewrite different parts of the application hoping
  that the problem disappears.

## Git

- Keep changes focused.
- Do not create Git commits unless explicitly asked.
- Never delete user data or application data without confirmation.
- Do not alter Git history unless explicitly instructed.

## Project decisions

PROJECT_SPEC.md is the source of truth for the application's
requirements and architectural decisions.

Do not contradict decisions recorded there without first discussing
the change.

## Uncertainty

If a decision would significantly affect the architecture, data model,
file structure or user experience and the correct choice is unclear,
stop and ask for clarification rather than making a major assumption.

For small implementation decisions, use the simplest solution
consistent with the existing architecture.

## Completion

A feature is considered complete when:

- It implements the requested behaviour.
- Relevant tests/checks pass.
- Existing functionality continues to work.
- The implementation is consistent with the project's architecture.
- No unnecessary changes have been introduced.