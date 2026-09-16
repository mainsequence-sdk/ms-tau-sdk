# ADR 0003: Tau-Native Project Configuration

Status: Accepted

Date: 2026-09-16

## Context

The SDK needs useful default agent behavior while allowing each project to own its behavior and
extensions. A parallel SDK prompt or extension configuration would create conflicting precedence.

## Decision

The SDK ships packaged Tau resources as defaults and delegates effective configuration resolution
to Tau. A project uses its normal `.tau` directory; there is no SDK-specific prompt manifest,
extension switch, or second configuration model.

The packaged `SYSTEM.md` is used when a project provides no replacement. A project
`.tau/SYSTEM.md` replaces that default through Tau's native precedence. Skills, prompt templates,
context, hooks, extensions, diagnostics, reload, and shutdown use Tau's native resource and
extension lifecycle.

Project resources and extensions are always enabled for the selected workspace. The host approves
project trust because the consuming project already owns and runs arbitrary code and dependencies
in the same Python process. The project is responsible for its extension code, dependency versions,
network behavior, and failures.

The SDK retains authority only over protocol and safety invariants that project behavior must not
forge: runtime credentials, authenticated transport, lease proof, caller identity, persistence
ordering, secret redaction, and wire validation.

## Consequences

- A project can replace the general Tau behavior without rebuilding the SDK.
- Broken project resources or extension code can prevent that project's process from starting or
  operating correctly.
- New Tau configuration capabilities belong upstream in Tau rather than in a competing SDK layer.
