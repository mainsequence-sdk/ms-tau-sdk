# Test Disposition Matrix

Status: Phases T0 through T5 complete

Date: 2026-09-16

Baseline: 271 passed, 1 live-credential test skipped.

Owner for every retained or rewritten test: Main Sequence TAU SDK. The table covers every tracked
test module and fixture family in this repository; deleted image-only tests are recorded below.

| Current test or family | Proposed class | Target action |
| --- | --- | --- |
| `tests/contract/test_http_surface.py` | SDK HTTP contract | Freeze the accepted operation set through the public application fixture. |
| `tests/contract/test_app.py` | Rewritten SDK contract | Exercise the built `ms_tau_sdk` application factory and route surface without importing `astro`. |
| `tests/contract/test_backend_client.py` | Portable contract + rewritten imports | Preserve backend wire/error/auth behavior through the new private client boundary. |
| `tests/contract/test_session_persistence_client.py` | Portable persistence contract | Verify the SDK storage/client batch contract. |
| `tests/contract/test_task_creation_replay.py` | Portable A2A contract | Preserve replay/idempotency behavior through the new A2A service. |
| `tests/e2e/test_real_conversation.py` | Rewritten SDK end-to-end | Docker/Compose control removed in C0. Keep the credential-gated HTTP/SSE behavior test, then rename its environment contract and run it against `ms-tau`. |
| `tests/unit/test_a2a.py` | Rewritten SDK test | Preserve accepted A2A behavior through new package paths. |
| `tests/unit/test_a2a_capabilities.py` | Rewritten/possibly reduced | Preserve wire-protocol capability metadata; remove retired product capability assumptions. |
| `tests/unit/test_a2a_task_api.py` | Rewritten SDK test | Preserve accepted task API behavior. |
| `tests/unit/test_a2a_task_lifecycle.py` | Portable contract + rewritten unit boundary | Govern under reissued ADR 54. |
| `tests/unit/test_a2a_task_replay.py` | Portable contract + rewritten unit boundary | Preserve replay/idempotency behavior. |
| `tests/unit/test_backend_auth.py` | Portable contract + rewritten imports | Preserve runtime credential exchange, refresh, and failure behavior. |
| `tests/unit/test_chat_api.py` | Rewritten SDK transport test | Preserve assistant-ui streaming and cancellation behavior. |
| `tests/unit/test_event_encoding.py` | Portable contract | Preserve accepted stream encoding independent of module path. |
| `tests/unit/test_llm_endpoint.py` | Retired by ADR 0007 | Delete with the agent-targeted one-shot response endpoint; assert route absence in the HTTP contract. |
| `tests/unit/test_logging.py` | Rewrite for new ontology | Preserve structured fields that remain operationally required; remove Astro/legacy-role assertions. |
| `tests/unit/test_mainsequence_mcp.py` | Rewritten SDK integration test | Preserve MCP discovery/tool/resource behavior. |
| `tests/unit/test_project_extensions.py` | Rewrite for one Tau configuration | Remove enable-flag contract; test verified-workspace native `.tau` precedence and lifecycle. |
| `tests/unit/test_provider_credentials.py` | Portable provider-control test | Preserve validation, refresh, and provider-construction behavior. |
| `tests/unit/test_real_conversation_ux.py` | Rewritten SDK behavior test | Preserve user-visible event sequencing and error behavior. |
| `tests/unit/test_runtime_manager.py` | SDK runtime test | Cover bootstrap, leases, snapshots, extensions, cancellation, and the conversational critical path. |
| `tests/unit/test_runtime_provenance.py` | Rewritten SDK test | Preserve provenance entries and durability behavior. |
| `tests/unit/test_session_api.py` | Rewritten SDK transport test | Preserve accepted session API behavior. |
| `tests/unit/test_session_storage.py` | Portable persistence test | Govern under reissued ADR 48 and SDK storage primitive. |
| `tests/unit/test_settings.py` | Replace | Define the new SDK settings contract; delete Astro-prefixed and optional-extension flag assertions. |
| `tests/unit/test_strict_json.py` | Portable contract | Preserve strict-JSON behavior used by A2A. |
| `tests/unit/test_system_prompt_policy.py` | Replace with Tau configuration tests | Stop locking an Astro-only prompt injector; verify SDK defaults and native project override precedence. |
| `tests/fixtures/a2a/` | Portable fixtures pending protocol audit | Version against reissued A2A decisions and generated OpenAPI/contract tests. |
| `tests/fixtures/sdk-consumer-project/` | Rewritten SDK consumer fixture | Independent locked Python project with a minimal application shim, project Tau configuration, and project-owned extension. |
| `tests/conftest.py` | Rewrite infrastructure | Remove implicit Astro import assumptions and support built-wheel/clean-project testing. |

## New Suites Required

- Wheel and sdist content/metadata inspection.
- Clean virtual-environment installation with no source checkout on `PYTHONPATH`.
- Minimal CodeRepository fixture running `ms-tau` from its root.
- Public API import and compatibility tests.
- One Tau configuration precedence suite.
- CLI startup/readiness/shutdown tests.
- Runtime composition/version reporting tests.
- Active ADR/docs terminology checks.

The former Docker/overlay recipe test was deleted with those assets during C0. It has no SDK test
replacement. Project-image and external deployment behavior are outside this test suite.

## Deletion Rule

A legacy test is removed only after its row names the eliminated decision and replacement evidence,
but no test remains permanently merely to force support for Astro images, imports, commands,
settings, no-workspace operation, or legacy roles.

## T0 Evidence Added

- The original migration surface test captured the documented OpenAPI operation list before source
  movement. Its reviewed successor is the permanent SDK HTTP surface contract.

## Cutover Evidence

- The bounded migration contract is now the permanent `test_http_surface.py` SDK contract.
- Production tests import only `ms_tau_sdk`; no compatibility namespace or command is exercised.
- The locked `sdk-consumer-project` fixture and clean-wheel run cover project and distribution use.
- The root suite, branch coverage, lint, formatting, and strict typing are the active gates.
