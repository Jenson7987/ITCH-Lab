"""TASK-002 cross-language stable-error catalogue test."""

import re
from pathlib import Path

from itchlab_research.errors import ErrorCode

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def test_task_002_cpp_and_python_error_catalogues_match() -> None:
    cpp_source = (REPOSITORY_ROOT / "cpp" / "src" / "core" / "errors.cpp").read_text(
        encoding="utf-8"
    )
    cpp_codes = set(re.findall(r'return "(ERR_[A-Z0-9_]+)";', cpp_source))
    python_codes = {code.value for code in ErrorCode}

    assert cpp_codes == python_codes
    assert len(python_codes) == len(ErrorCode)
