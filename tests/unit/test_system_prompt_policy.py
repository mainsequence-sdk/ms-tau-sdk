from tau_coding.resources import discover_system_prompt_resources

from ms_tau_sdk.resources.loader import resource_root, tau_resource_paths


def test_packaged_system_prompt_is_the_default(tmp_path) -> None:
    resources = discover_system_prompt_resources(
        tau_resource_paths(tmp_path, state_home=tmp_path / "state")
    )

    assert resources.custom_prompt_path == resource_root() / "SYSTEM.md"
    assert "Main Sequence TAU SDK" in (resources.custom_prompt or "")


def test_project_system_prompt_replaces_the_packaged_default(tmp_path) -> None:
    project_tau = tmp_path / ".tau"
    project_tau.mkdir()
    project_system = project_tau / "SYSTEM.md"
    project_system.write_text("Project-owned Tau behavior.", encoding="utf-8")

    resources = discover_system_prompt_resources(
        tau_resource_paths(tmp_path, state_home=tmp_path / "state")
    )

    assert resources.custom_prompt == "Project-owned Tau behavior."
    assert resources.custom_prompt_path == project_system
    assert resource_root() / "SYSTEM.md" not in resources.append_prompt_paths


def test_project_append_system_uses_tau_native_composition(tmp_path) -> None:
    project_tau = tmp_path / ".tau"
    project_tau.mkdir()
    project_append = project_tau / "APPEND_SYSTEM.md"
    project_append.write_text("Project-specific addition.", encoding="utf-8")

    resources = discover_system_prompt_resources(
        tau_resource_paths(tmp_path, state_home=tmp_path / "state")
    )

    assert resources.append_prompt == "Project-specific addition."
    assert resources.append_prompt_paths == (project_append,)
