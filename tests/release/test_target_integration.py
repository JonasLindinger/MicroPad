# AI-assisted development — firmware, HA automation, config generator and docs were created with AI (LLM) help, reviewed and tested by the author. Provided as-is, without warranty; verify on your own hardware, don't use for safety-critical applications.
"""Integration Task 10: fresh-target dry-run integration tests.

These exercise :func:`scripts.integrate_public_repo.prepare_replacement` against a
synthetic *local* two-commit target repo (``fake_target_repo``) and a minimal
clean-room ``clean_source``. They assert that a dry run clones exactly ``main``,
preserves hardware assets and the license by blob hash, removes every other old
software tracked path, adds the clean-room manifest, never commits, and produces
review evidence. Nothing here touches the network and no fixture carries
personal data.
"""

import subprocess
from pathlib import Path

import pytest

import scripts.integrate_public_repo as integration
from scripts.integrate_public_repo import IntegrationError, prepare_replacement


def test_replacement_preserves_history_hardware_and_license(tmp_path, fake_target_repo, clean_source):
    before = fake_target_repo.snapshot(["PCB", "STL Files", "LICENSE"])

    result = prepare_replacement(
        source=clean_source,
        destination=tmp_path / "integration",
        clone_url=fake_target_repo.url,
        dry_run=True,
    )

    assert result.commit_count_added == 0
    assert result.preserved_before == before
    assert result.preserved_after == before
    assert "old_firmware.ino" in result.removed_paths
    assert (result.destination / "firmware/MicroPad_HA_Controller.ino").is_file()
    assert result.unexpected_paths == ()
    # README is a noticed clean-room file: it replaces the old one.
    assert (result.destination / "README.md").read_text(encoding="utf-8") == "# Clean-room MicroPad\n"


def test_replacement_commit_message_matches_constant_and_has_final_newline():
    message = (
        Path(__file__).resolve().parents[2] / "release" / "replacement-commit-message.txt"
    ).read_text(encoding="utf-8")
    assert message == integration.REPLACEMENT_COMMIT_MESSAGE
    assert message.endswith("\n") and not message.endswith("\n\n")


def test_old_software_content_never_enters_evidence(tmp_path, target_repo_factory, clean_source):
    canary = "OLD" + "_SOFTWARE_CONTENT_CANARY_7f36c9"
    files = [
        (path, canary + "\n" if path == "old_firmware.ino" else content, group)
        for path, content, group in _default_target_files()
    ]
    repo = target_repo_factory(files)
    evidence = tmp_path / "evidence"

    result = prepare_replacement(
        clean_source, tmp_path / "destination", repo.url, True, evidence
    )

    assert "old_firmware.ino" in result.removed_paths
    assert "old_firmware.ino\n" in (evidence / "removed-paths.txt").read_text(encoding="utf-8")
    for evidence_file in evidence.iterdir():
        assert canary.encode() not in evidence_file.read_bytes(), evidence_file.name
    candidate_diff = (evidence / "candidate.diff").read_text(encoding="utf-8")
    assert "--- /dev/null" in candidate_diff
    assert "+# Clean-room MicroPad" in candidate_diff


def test_candidate_diff_reconstructs_every_manifest_byte_from_empty_tree(
    tmp_path, fake_target_repo, clean_source
):
    manifest_file = clean_source / "release" / "software-files.txt"
    manifest_file.write_text(
        manifest_file.read_text(encoding="utf-8") + "empty.txt\n", encoding="utf-8"
    )
    (clean_source / "empty.txt").touch()
    evidence = tmp_path / "evidence"
    prepare_replacement(
        clean_source, tmp_path / "destination", fake_target_repo.url, True, evidence
    )
    review_tree = tmp_path / "review-tree"
    review_tree.mkdir()

    subprocess.run(
        ["git", "-C", str(review_tree), "apply", "--binary", str(evidence / "candidate.diff")],
        check=True, capture_output=True, text=True,
    )

    manifest = manifest_file.read_text(encoding="utf-8").splitlines()
    assert sorted(path.relative_to(review_tree).as_posix() for path in review_tree.rglob("*") if path.is_file()) == sorted(manifest)
    for relative in manifest:
        assert (review_tree / relative).read_bytes() == (clean_source / relative).read_bytes()


def test_missing_pcb_assets(tmp_path, target_repo_factory, clean_source):
    files = [f for f in _default_target_files() if not f[0].startswith("PCB")]
    repo = target_repo_factory(files)
    with pytest.raises(IntegrationError, match="PCB"):
        prepare_replacement(clean_source, tmp_path / "dst", repo.url, True)


def test_missing_stl_assets(tmp_path, target_repo_factory, clean_source):
    files = [f for f in _default_target_files() if not f[0].startswith("STL Files")]
    repo = target_repo_factory(files)
    with pytest.raises(IntegrationError, match="STL Files"):
        prepare_replacement(clean_source, tmp_path / "dst", repo.url, True)


def test_zero_license_candidates(tmp_path, target_repo_factory, clean_source):
    files = [f for f in _default_target_files() if f[0] != "LICENSE"]
    repo = target_repo_factory(files)
    with pytest.raises(IntegrationError, match="license"):
        prepare_replacement(clean_source, tmp_path / "dst", repo.url, True)


def test_multiple_license_candidates(tmp_path, target_repo_factory, clean_source):
    files = [*_default_target_files(), ("COPYING.txt", "GPL (synthetic)\n", 3)]
    repo = target_repo_factory(files)
    with pytest.raises(IntegrationError, match="license"):
        prepare_replacement(clean_source, tmp_path / "dst", repo.url, True)


@pytest.mark.parametrize("preserved_path", ["LICENSE", "PCB/board.kicad_pcb"])
def test_manifest_cannot_replace_preserved_assets(
    tmp_path, fake_target_repo, clean_source, preserved_path
):
    manifest_file = clean_source / "release" / "software-files.txt"
    manifest_file.write_text(
        manifest_file.read_text(encoding="utf-8") + preserved_path + "\n",
        encoding="utf-8",
    )
    replacement = clean_source / preserved_path
    replacement.parent.mkdir(parents=True, exist_ok=True)
    replacement.write_text("clean-room replacement must be rejected\n", encoding="utf-8")
    evidence = tmp_path / "evidence"

    with pytest.raises(IntegrationError, match="preserved"):
        prepare_replacement(
            clean_source, tmp_path / "dst", fake_target_repo.url, True, evidence
        )

    assert not evidence.exists()


@pytest.mark.parametrize("reserved_path", ["PCB/new-cleanroom.txt", "STL Files/new.stl"])
def test_manifest_cannot_add_paths_under_reserved_namespaces(
    tmp_path, fake_target_repo, clean_source, monkeypatch, reserved_path
):
    manifest_file = clean_source / "release" / "software-files.txt"
    manifest_file.write_text(
        manifest_file.read_text(encoding="utf-8") + reserved_path + "\n",
        encoding="utf-8",
    )
    addition = clean_source / reserved_path
    addition.parent.mkdir(parents=True, exist_ok=True)
    addition.write_text("new clean-room asset must be rejected\n", encoding="utf-8")
    evidence = tmp_path / "evidence"
    copied = False

    def record_copy(*args, **kwargs):
        nonlocal copied
        copied = True

    monkeypatch.setattr(integration, "_copy_manifest", record_copy)

    with pytest.raises(IntegrationError, match="preserved"):
        prepare_replacement(
            clean_source, tmp_path / "dst", fake_target_repo.url, True, evidence
        )

    assert copied is False
    assert not evidence.exists()


@pytest.mark.parametrize(
    ("manifest_entry", "source_path"),
    [
        ("PCB/./new-cleanroom.txt", "PCB/new-cleanroom.txt"),
        ("PCB//new-cleanroom.txt", "PCB/new-cleanroom.txt"),
        ("PCB/../PCB/new-cleanroom.txt", "PCB/new-cleanroom.txt"),
        (r"PCB\new-cleanroom.txt", "PCB/new-cleanroom.txt"),
        (r"STL Files\new.stl", "STL Files/new.stl"),
    ],
)
def test_reserved_namespace_guard_uses_normalized_platform_independent_paths(
    tmp_path, fake_target_repo, clean_source, monkeypatch, manifest_entry, source_path
):
    manifest_file = clean_source / "release" / "software-files.txt"
    manifest_file.write_text(
        manifest_file.read_text(encoding="utf-8") + manifest_entry + "\n",
        encoding="utf-8",
    )
    addition = clean_source / source_path
    addition.parent.mkdir(parents=True, exist_ok=True)
    addition.write_text("normalized reserved path must be rejected\n", encoding="utf-8")
    copied = False

    def record_copy(*args, **kwargs):
        nonlocal copied
        copied = True

    monkeypatch.setattr(integration, "_copy_manifest", record_copy)

    with pytest.raises(IntegrationError, match="preserved"):
        prepare_replacement(
            clean_source, tmp_path / "dst", fake_target_repo.url, True,
            tmp_path / "evidence",
        )

    assert copied is False
    assert not (tmp_path / "evidence").exists()


def test_unrelated_path_with_pcb_in_filename_is_allowed(
    tmp_path, fake_target_repo, clean_source
):
    manifest_file = clean_source / "release" / "software-files.txt"
    manifest_file.write_text(
        manifest_file.read_text(encoding="utf-8") + "docs/PCB-guide.md\n",
        encoding="utf-8",
    )
    guide = clean_source / "docs" / "PCB-guide.md"
    guide.parent.mkdir(parents=True)
    guide.write_text("clean-room guide\n", encoding="utf-8")

    result = prepare_replacement(
        clean_source, tmp_path / "dst", fake_target_repo.url, True,
        tmp_path / "evidence",
    )

    assert "docs/PCB-guide.md" in result.added_paths


def test_manifest_path_outside_source(tmp_path, fake_target_repo):
    source = tmp_path / "source"
    (source / "release").mkdir(parents=True)
    (source / "release" / "software-files.txt").write_text(
        "../outside.txt\n", encoding="utf-8"
    )
    outside = tmp_path / "outside.txt"
    outside.write_text("not in the source\n", encoding="utf-8")
    with pytest.raises(IntegrationError, match="outside"):
        prepare_replacement(source, tmp_path / "dst", fake_target_repo.url, True)


def test_symlink_escaping_source(tmp_path, fake_target_repo):
    outside = tmp_path / "outside.txt"
    outside.write_text("secret\n", encoding="utf-8")
    source = tmp_path / "source"
    (source / "release").mkdir(parents=True)
    (source / "release" / "software-files.txt").write_text(
        "firmware/slink.ino\n", encoding="utf-8"
    )
    (source / "firmware").mkdir(parents=True)
    (source / "firmware" / "slink.ino").symlink_to(outside)
    with pytest.raises(IntegrationError, match="symlink"):
        prepare_replacement(source, tmp_path / "dst", fake_target_repo.url, True)


def test_dirty_destination(tmp_path, fake_target_repo, clean_source):
    dest = tmp_path / "dst"
    dest.mkdir()
    (dest / "leftover.txt").write_text("dirty\n", encoding="utf-8")
    with pytest.raises(IntegrationError, match="destination"):
        prepare_replacement(clean_source, dest, fake_target_repo.url, True)


def test_preexisting_git_checkout_is_rejected_even_when_clean(
    tmp_path, fake_target_repo, clean_source
):
    dest = tmp_path / "dst"
    subprocess.run(
        ["git", "clone", fake_target_repo.url, str(dest)],
        check=True, capture_output=True, text=True,
    )

    before = subprocess.run(
        ["git", "-C", str(dest), "status", "--porcelain=v1"],
        check=True, capture_output=True, text=True,
    ).stdout
    with pytest.raises(IntegrationError, match="fresh destination"):
        prepare_replacement(clean_source, dest, fake_target_repo.url, True)
    after = subprocess.run(
        ["git", "-C", str(dest), "status", "--porcelain=v1"],
        check=True, capture_output=True, text=True,
    ).stdout
    assert before == after == ""


def test_main_advancing_during_copy_aborts_without_evidence(
    tmp_path, fake_target_repo, clean_source, monkeypatch
):
    original_copy = integration._copy_manifest

    def copy_then_advance(source, destination, manifest):
        original_copy(source, destination, manifest)
        fake_target_repo.advance_main()

    monkeypatch.setattr(integration, "_copy_manifest", copy_then_advance)
    evidence = tmp_path / "evidence"

    with pytest.raises(IntegrationError, match="advanced during preparation"):
        prepare_replacement(
            clean_source, tmp_path / "destination", fake_target_repo.url, True, evidence
        )
    assert not evidence.exists()


def test_preserved_blob_mutation_aborts_without_success_evidence(
    tmp_path, fake_target_repo, clean_source, monkeypatch
):
    original_copy = integration._copy_manifest
    preserved_path = "PCB/board.kicad_pcb"
    before = fake_target_repo.snapshot(["PCB"])[preserved_path]

    def copy_then_mutate(source, destination, manifest):
        original_copy(source, destination, manifest)
        (destination / preserved_path).write_text("mutated preserved blob\n", encoding="utf-8")

    monkeypatch.setattr(integration, "_copy_manifest", copy_then_mutate)
    destination = tmp_path / "destination"
    evidence = tmp_path / "evidence"

    with pytest.raises(IntegrationError) as raised:
        prepare_replacement(clean_source, destination, fake_target_repo.url, True, evidence)

    after = subprocess.run(
        ["git", "-C", str(destination), "hash-object", "--", preserved_path],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    message = str(raised.value)
    assert preserved_path in message
    assert before in message
    assert after in message
    assert not evidence.exists()


def _default_target_files():
    from tests.release.conftest import _DEFAULT_TARGET_FILES

    return [(p, c, g) for p, c, g in _DEFAULT_TARGET_FILES]