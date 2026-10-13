"""The training CLI explains itself when the out-of-tree libraries are missing."""

import pytest

from ml.__main__ import MISSING_LIBRARIES, require_libraries


def test_a_missing_training_library_is_explained_not_a_traceback(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as exit_info:
        require_libraries("sys", "no_such_training_library")

    assert exit_info.value.code == 2
    message = capsys.readouterr().err
    assert "no_such_training_library missing" in message
    assert "pip install numpy scipy lightgbm" in message
    assert "Company Management approval" in message
    assert MISSING_LIBRARIES.split("{")[0] in message


def test_installed_libraries_pass_silently(capsys: pytest.CaptureFixture[str]) -> None:
    require_libraries("sys", "json")
    assert capsys.readouterr().err == ""
