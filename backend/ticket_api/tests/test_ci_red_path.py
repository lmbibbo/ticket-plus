"""Temporary red-path check for the CI pipeline. Removed after verification."""


def test_ci_red_path() -> None:
    assert False, "temporary failure to verify that CI blocks the merge"
