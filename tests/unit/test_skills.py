from __future__ import annotations

import json
from pathlib import Path

import pytest

from ms_tau_sdk import cli
from ms_tau_sdk.skills import (
    PIN_FILE_NAME,
    SkillSyncError,
    list_packaged_skills,
    resolve_packaged_skill,
    sync_skills,
)

EXPECTED_SKILLS = (
    "tau_a2a_runtime_adapter",
    "tau_local_development",
    "tau_project_customization",
    "tau_repository_integration",
)


def test_packaged_skill_catalog_is_complete() -> None:
    assert list_packaged_skills() == EXPECTED_SKILLS
    for skill in EXPECTED_SKILLS:
        path = resolve_packaged_skill(skill)
        assert path.name == "SKILL.md"
        assert path.is_file()


def test_a2a_skill_explains_deployed_and_local_readiness() -> None:
    skill = resolve_packaged_skill("tau_a2a_runtime_adapter").read_text(encoding="utf-8")

    assert "use the projected `a2a.send_message` MCP operation" in skill
    assert "waits for its runtime to become ready" in skill
    assert "use the returned Task handle with `a2a.wait_task`" in skill
    assert "wait for its local `/ready` result" in skill


def test_local_development_skill_defines_the_complete_a2a_boundary() -> None:
    skill = resolve_packaged_skill("tau_local_development").read_text(encoding="utf-8")
    normalized = " ".join(skill.split())

    required_contract_terms = (
        "not an offline, mock-provider, or mock-MCP mode",
        "does not require a platform AgentSession",
        "`POST /api/a2a/v1/message:send` and JSON-RPC `message/send`",
        "`POST /api/a2a/v1/message:stream` and JSON-RPC `message/stream`",
        "`input_required` or `auth_required`",
        "surviving process restart",
        "do not need managed-gateway `X-Caller-*` headers",
        '`completion_policy: "poll"`',
        '`completion_policy: "resume_caller"`',
        "must not return `local_mode_capability_unsupported`",
    )
    for term in required_contract_terms:
        assert term in normalized


def test_sync_replaces_only_sdk_namespace_and_removes_retired_skills(tmp_path: Path) -> None:
    mainsequence_skill = tmp_path / ".agents/skills/mainsequence/keep/SKILL.md"
    mainsequence_skill.parent.mkdir(parents=True)
    mainsequence_skill.write_text("platform", encoding="utf-8")
    retired = tmp_path / ".agents/skills/ms_tau_sdk/retired/SKILL.md"
    retired.parent.mkdir(parents=True)
    retired.write_text("retired", encoding="utf-8")
    repository_skill = tmp_path / ".agents/skills/repository-owned/SKILL.md"
    repository_skill.parent.mkdir(parents=True)
    repository_skill.write_text("repository", encoding="utf-8")

    result = sync_skills(tmp_path)

    destination = Path(result.destination)
    assert result.skills == EXPECTED_SKILLS
    assert not (destination / "retired").exists()
    assert mainsequence_skill.read_text(encoding="utf-8") == "platform"
    assert repository_skill.read_text(encoding="utf-8") == "repository"
    pin = (destination / PIN_FILE_NAME).read_text(encoding="utf-8")
    assert "library_name=ms-tau-sdk" in pin
    assert "namespace=ms_tau_sdk" in pin
    assert f"pinned_version={result.installed_version}" in pin
    assert "command=ms-tau skills sync --path ." in pin
    customization = (destination / "tau_project_customization" / "SKILL.md").read_text()
    assert "TAU_EXCLUDE_BASE_TOOLS" in customization
    assert "TAU_EXCLUDE_MAINSEQUENCE_MCP" in customization
    assert "harness_agent.spec.env_vars" in customization
    assert "task_request_input" in customization


def test_sync_dry_run_does_not_create_agents_directory(tmp_path: Path) -> None:
    result = sync_skills(tmp_path, dry_run=True)

    assert result.dry_run is True
    assert not (tmp_path / ".agents").exists()


def test_sync_rejects_symlink_destination(tmp_path: Path) -> None:
    external = tmp_path / "external"
    external.mkdir()
    destination = tmp_path / ".agents/skills/ms_tau_sdk"
    destination.parent.mkdir(parents=True)
    destination.symlink_to(external, target_is_directory=True)

    with pytest.raises(SkillSyncError, match="symbolic link"):
        sync_skills(tmp_path)


def test_cli_no_arguments_preserves_server_startup(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[bool] = []
    monkeypatch.setattr(cli, "run", lambda: calls.append(True))

    assert cli.main([]) == 0
    assert calls == [True]


def test_cli_lists_and_synchronizes_skills_as_json(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(["skills", "list", "--json"]) == 0
    listed = json.loads(capsys.readouterr().out)
    assert tuple(listed["skills"]) == EXPECTED_SKILLS

    assert cli.main(["skills", "sync", "--path", str(tmp_path), "--json"]) == 0
    synchronized = json.loads(capsys.readouterr().out)
    assert Path(synchronized["destination"]).is_dir()
    assert tuple(synchronized["skills"]) == EXPECTED_SKILLS


def test_cli_reports_unknown_skill_without_traceback(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["skills", "path", "missing"]) == 2
    captured = capsys.readouterr()
    assert "Unknown ms-tau-sdk skill" in captured.err
    assert "Traceback" not in captured.err
