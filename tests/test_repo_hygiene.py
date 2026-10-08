"""Code public, data never: fail if personal data could reach the public repository."""

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).parent.parent

FORBIDDEN_FILES = re.compile(r"(\.sqlite3?|\.db|\.pdf|\.env)$|(^|/)data/")
# Italian codice fiscale shape; fixtures may only use the invented ones listed here.
CODICE_FISCALE = re.compile(r"\b[A-Z]{6}\d{2}[A-EHLMPRST]\d{2}[A-Z]\d{3}[A-Z]\b")
INVENTED_CF = {
    "PRZGRG50A01D612X",
    "MLNTTT90A41D612Y",
    "MLNRBL40A01D612X",
    "NCCCMN95A41D612Y",
}
IBAN = re.compile(r"\bIT\d{2}[A-Z]\d{10}[0-9A-Z]{12}\b")


def tracked_files() -> list[str]:
    result = subprocess.run(
        ["git", "-c", "safe.directory=*", "ls-files"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.splitlines()


def test_no_data_files_are_tracked() -> None:
    assert [f for f in tracked_files() if FORBIDDEN_FILES.search(f)] == []


def test_gitignore_covers_data() -> None:
    ignore = (ROOT / ".gitignore").read_text()
    for pattern in ("*.sqlite", "*.pdf", "/data/", ".env"):
        assert pattern in ignore


def test_only_invented_codici_fiscali_and_no_iban_in_tracked_text() -> None:
    found: list[str] = []
    for name in tracked_files():
        path = ROOT / name
        if not path.is_file():
            continue
        try:
            text = path.read_text()
        except UnicodeDecodeError:
            continue
        found += [f"{name}: {cf}" for cf in CODICE_FISCALE.findall(text) if cf not in INVENTED_CF]
        found += [f"{name}: IBAN" for _ in IBAN.findall(text)]
    assert found == []
