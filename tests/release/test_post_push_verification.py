# AI-assisted development — firmware, HA automation, config generator and docs were created with AI (LLM) help, reviewed and tested by the author. Provided as-is, without warranty; verify on your own hardware, don't use for safety-critical applications.
"""Authoritative public-repository verification, entirely with synthetic local data."""

import copy
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from scripts.verify_public_repo import main, parse_github_branch, parse_github_tree, verify_snapshot


def test_verified_snapshot_requires_one_commit_and_exact_tree(valid_snapshot):
    result = verify_snapshot(valid_snapshot)
    assert result["commit_count"] == 1
    assert result["local_sha"] == result["github_main_sha"]
    assert result["candidate_tree_sha256"] == result["anonymous_tree_sha256"]
    assert result["preserved_before"] == result["preserved_after"]


@pytest.mark.parametrize("field", [
    "github_main_sha", "anonymous_tree_sha256", "preserved_after", "public_audit_ok"
])
def test_any_authoritative_divergence_fails(valid_snapshot, field):
    valid_snapshot[field] = False if field == "public_audit_ok" else "different"
    with pytest.raises(ValueError, match=field):
        verify_snapshot(valid_snapshot)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("commit_count", 0),
        ("commit_count", 2),
        ("local_sha", "f" * 39),
        ("github_main_sha", "f" * 40),
        ("candidate_tree_sha256", "a" * 63),
        ("github_tree_sha256", "f" * 64),
        ("parent_shas", []),
        ("parent_shas", ["1" * 40, "0" * 40]),
        ("parent_shas", ["0" * 40]),
        ("github_tree_complete", False),
    ],
)
def test_commit_sha_tree_and_parent_divergence_fails(valid_snapshot, field, value):
    valid_snapshot[field] = value
    with pytest.raises(ValueError, match=field):
        verify_snapshot(valid_snapshot)


@pytest.mark.parametrize("field", ["candidate_paths", "anonymous_paths", "github_paths"])
def test_missing_manifest_path_fails(valid_snapshot, field):
    valid_snapshot[field].remove("README.md")
    with pytest.raises(ValueError, match=field):
        verify_snapshot(valid_snapshot)


@pytest.mark.parametrize("field", ["candidate_paths", "anonymous_paths", "github_paths"])
def test_extra_path_fails(valid_snapshot, field):
    valid_snapshot[field].append("unexpected.txt")
    with pytest.raises(ValueError, match=field):
        verify_snapshot(valid_snapshot)


@pytest.mark.parametrize("field", ["manifest_paths", "candidate_paths", "anonymous_paths", "github_paths"])
def test_duplicate_path_fails(valid_snapshot, field):
    valid_snapshot[field].append(valid_snapshot[field][0])
    with pytest.raises(ValueError, match=field):
        verify_snapshot(valid_snapshot)


def test_preserved_blob_sha_divergence_fails(valid_snapshot):
    valid_snapshot["preserved_after"]["LICENSE"] = "0" * 40
    with pytest.raises(ValueError, match="preserved_after"):
        verify_snapshot(valid_snapshot)


@pytest.mark.parametrize(
    "url",
    [
        "https://user:" + "password@example.test/repo.git",
        "https://x-access-" + "token:synthetic@example.test/repo.git",
        "https://example.test/repo.git?token=synthetic",
        "https://ghp_" + "x" * 36 + "@example.test/repo.git",
        "ssh://user:" + "password@example.test/repo.git",
    ],
)
@pytest.mark.parametrize("field", ["fetch_urls", "push_urls"])
def test_credential_bearing_remote_fails(valid_snapshot, field, url):
    valid_snapshot[field] = [url]
    with pytest.raises(ValueError, match=field):
        verify_snapshot(valid_snapshot)


def test_credential_words_in_repository_path_are_not_misread_as_userinfo(valid_snapshot):
    valid_snapshot["fetch_urls"] = ["https://github.com/example/token-tools.git"]
    assert verify_snapshot(valid_snapshot) is valid_snapshot


def test_github_branch_parser_requires_strict_sha_parent_and_tree():
    parsed = parse_github_branch({
        "commit": {
            "sha": "2" * 40,
            "commit": {"tree": {"sha": "7" * 40}},
            "parents": [{"sha": "1" * 40}],
        }
    })
    assert parsed == ("2" * 40, "7" * 40, ["1" * 40])


@pytest.mark.parametrize(
    "mutation",
    [
        lambda value: value.pop("commit"),
        lambda value: value["commit"].__setitem__("sha", "not-a-sha"),
        lambda value: value["commit"].__setitem__("parents", []),
        lambda value: value["commit"]["commit"]["tree"].__setitem__("sha", "f" * 39),
    ],
)
def test_github_branch_parser_rejects_malformed_json(mutation):
    value = {
        "commit": {
            "sha": "2" * 40,
            "commit": {"tree": {"sha": "7" * 40}},
            "parents": [{"sha": "1" * 40}],
        }
    }
    mutation(value)
    with pytest.raises(ValueError, match="github_main"):
        parse_github_branch(value)


def _github_tree():
    return {
        "sha": "7" * 40,
        "truncated": False,
        "tree": [
            {"path": "README.md", "mode": "100644", "type": "blob", "sha": "8" * 40},
            {"path": "firmware", "mode": "040000", "type": "tree", "sha": "9" * 40},
            {"path": "firmware/main.ino", "mode": "100755", "type": "blob", "sha": "a" * 40},
        ],
    }


def test_github_tree_parser_returns_canonical_blob_records():
    root_sha, records, entry_count = parse_github_tree(_github_tree())
    assert root_sha == "7" * 40
    assert records == [
        ("README.md", "100644", "8" * 40),
        ("firmware/main.ino", "100755", "a" * 40),
    ]
    assert entry_count == 3


@pytest.mark.parametrize("truncated", [True, 1, None])
def test_github_tree_parser_rejects_incomplete_or_truncated_tree(truncated):
    value = _github_tree()
    value["truncated"] = truncated
    with pytest.raises(ValueError, match="github_tree_complete"):
        parse_github_tree(value)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda tree: tree["tree"].append(copy.deepcopy(tree["tree"][0])),
        lambda tree: tree["tree"][0].__setitem__("path", "../README.md"),
        lambda tree: tree["tree"][0].__setitem__("mode", "100600"),
        lambda tree: tree["tree"][0].__setitem__("sha", "b" * 39),
        lambda tree: tree["tree"][0].__setitem__("type", "commit"),
    ],
)
def test_github_tree_parser_rejects_duplicate_paths_wrong_modes_and_blob_shas(mutate):
    value = _github_tree()
    mutate(value)
    with pytest.raises(ValueError, match="github_tree"):
        parse_github_tree(value)


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def _commit(repo: Path, message: str) -> None:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", message)


def _authoritative_fixture(tmp_path: Path) -> dict[str, Path]:
    source = tmp_path / "source"
    source.mkdir()
    _git(source, "init", "--initial-branch", "main")
    _git(source, "config", "user.name", "Synthetic Fixture")
    _git(source, "config", "user.email", "fixture@example.test")
    (source / "release").mkdir()
    (source / "firmware").mkdir()
    manifest = source / "release" / "software-files.txt"
    manifest.write_text("README.md\nfirmware/main.ino\n", encoding="utf-8")
    (source / "README.md").write_text("# Synthetic release\n", encoding="utf-8")
    firmware = source / "firmware" / "main.ino"
    firmware.write_text("// synthetic firmware\n", encoding="utf-8")
    firmware.chmod(0o755)
    _commit(source, "synthetic source")

    candidate = tmp_path / "candidate"
    candidate.mkdir()
    _git(candidate, "init", "--initial-branch", "main")
    _git(candidate, "config", "user.name", "Synthetic Fixture")
    _git(candidate, "config", "user.email", "fixture@example.test")
    for relative, content in {
        "LICENSE": "synthetic license\n",
        "PCB/board.kicad_pcb": "synthetic board\n",
        "STL Files/case.stl": "synthetic case\n",
        "old-placeholder.txt": "synthetic removed path\n",
    }.items():
        path = candidate / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    os.symlink("synthetic-target", candidate / "old-link")
    _commit(candidate, "synthetic base")
    base_sha = _git(candidate, "rev-parse", "HEAD")
    preserved = {
        path: _git(candidate, "rev-parse", f"HEAD:{path}")
        for path in ("LICENSE", "PCB/board.kicad_pcb", "STL Files/case.stl")
    }
    (candidate / "old-link").unlink()
    (candidate / "old-placeholder.txt").unlink()
    shutil.copy2(source / "README.md", candidate / "README.md")
    (candidate / "firmware").mkdir()
    shutil.copy2(firmware, candidate / "firmware" / "main.ino")
    _commit(candidate, "synthetic replacement")

    anonymous = tmp_path / "anonymous"
    subprocess.run(
        ["git", "clone", "--quiet", str(candidate), str(anonymous)], check=True
    )
    for repo in (candidate, anonymous):
        if _git(repo, "remote") != "origin":
            _git(repo, "remote", "add", "origin", "https://github.com/example/project.git")
        else:
            _git(repo, "remote", "set-url", "origin", "https://github.com/example/project.git")
        _git(repo, "remote", "set-url", "--push", "origin", "git@github.com:example/project.git")

    evidence = tmp_path / "evidence"
    evidence.mkdir()
    (evidence / "base.sha").write_text(base_sha + "\n", encoding="utf-8")
    for name in ("preserved-before.json", "preserved-after.json"):
        (evidence / name).write_text(json.dumps(preserved) + "\n", encoding="utf-8")
    (evidence / "removed-paths.txt").write_text(
        "old-link\nold-placeholder.txt\n", encoding="utf-8"
    )
    final_paths = _git(candidate, "ls-tree", "-r", "--name-only", "HEAD").splitlines()
    (evidence / "target-tree.txt").write_text("\n".join(final_paths) + "\n", encoding="utf-8")

    final_sha = _git(candidate, "rev-parse", "HEAD")
    root_tree_sha = _git(candidate, "rev-parse", "HEAD^{tree}")
    branch = {
        "commit": {
            "sha": final_sha,
            "commit": {"tree": {"sha": root_tree_sha}},
            "parents": [{"sha": base_sha}],
        }
    }
    entries = []
    raw_tree = _git(candidate, "ls-tree", "-r", "-t", "HEAD").splitlines()
    for line in raw_tree:
        metadata, path = line.split("\t", 1)
        mode, kind, sha = metadata.split()
        entries.append({"path": path, "mode": mode, "type": kind, "sha": sha})
    github_tree = {"sha": root_tree_sha, "truncated": False, "tree": entries}
    github_main_file = tmp_path / "github-main.json"
    github_tree_file = tmp_path / "github-tree.json"
    github_main_file.write_text(json.dumps(branch) + "\n", encoding="utf-8")
    github_tree_file.write_text(json.dumps(github_tree) + "\n", encoding="utf-8")
    audit = tmp_path / "anonymous-audit.json"
    audit.write_text('{"clean": true, "findings": {}, "files_scanned": 5}\n', encoding="utf-8")
    return {
        "source": source, "manifest": manifest, "candidate": candidate,
        "anonymous": anonymous, "evidence": evidence, "github_main": github_main_file,
        "github_tree": github_tree_file, "audit": audit,
    }


def _cli_args(paths: dict[str, Path], output: Path) -> list[str]:
    return [
        "--candidate", str(paths["candidate"]),
        "--candidate-evidence", str(paths["evidence"]),
        "--anonymous", str(paths["anonymous"]),
        "--github-main", str(paths["github_main"]),
        "--github-tree", str(paths["github_tree"]),
        "--source", str(paths["source"]),
        "--manifest", str(paths["manifest"]),
        "--public-audit", str(paths["audit"]),
        "--output", str(output),
    ]


def test_cli_verifies_local_candidate_anonymous_and_captured_github_tree(tmp_path):
    paths = _authoritative_fixture(tmp_path)
    output = tmp_path / "verification.json"

    assert main(_cli_args(paths, output)) == 0

    result = json.loads(output.read_text(encoding="utf-8"))
    assert result["commit_count"] == 1
    assert result["manifest_count"] == 2
    assert result["preserved_blob_count"] == 3
    assert result["removed_path_count"] == 2
    assert result["audit_finding_count"] == 0


@pytest.mark.parametrize(("field", "replacement"), [("mode", "100755"), ("sha", "b" * 40)])
def test_cli_rejects_validly_formed_github_mode_or_blob_divergence(
    tmp_path, field, replacement
):
    paths = _authoritative_fixture(tmp_path)
    tree = json.loads(paths["github_tree"].read_text(encoding="utf-8"))
    readme = next(entry for entry in tree["tree"] if entry["path"] == "README.md")
    readme[field] = replacement
    paths["github_tree"].write_text(json.dumps(tree) + "\n", encoding="utf-8")
    output = tmp_path / "verification.json"

    assert main(_cli_args(paths, output)) == 1
    assert not output.exists()


def test_cli_rejects_removed_path_metadata_divergence(tmp_path):
    paths = _authoritative_fixture(tmp_path)
    (paths["evidence"] / "removed-paths.txt").write_text("different-path.txt\n", encoding="utf-8")
    output = tmp_path / "verification.json"

    assert main(_cli_args(paths, output)) == 1
    assert not output.exists()


def test_verifier_never_regenerates_or_compares_raw_target_diff():
    text = (Path(__file__).resolve().parents[2] / "scripts" / "verify_public_repo.py").read_text(
        encoding="utf-8"
    )
    assert "git diff --binary" not in text
    assert "candidate.diff" not in text