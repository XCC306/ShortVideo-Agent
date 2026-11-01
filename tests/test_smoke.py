"""Check the public module entrypoint without network or credentials."""

from pathlib import Path
import subprocess
import sys

import pytest


@pytest.mark.parametrize(
    ("arguments", "exit_code", "expected"),
    [
        (["--help"], 0, "--input"),
        ([], 2, "--input"),
        (["--input", "does-not-exist.json"], 2, "输入文件无效"),
    ],
)
def test_cli_scaffold(arguments: list[str], exit_code: int, expected: str) -> None:
    result = subprocess.run(
        [sys.executable, "-m", "src.main", *arguments],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == exit_code
    assert expected in result.stdout + result.stderr
