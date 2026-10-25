"""Run document parsing in a resource-limited child process.

PDF and DOCX parsers are large attack surfaces (decompression bombs,
pathological object graphs, parser bugs). Parsing in a subprocess with CPU,
memory and wall-clock limits means a malicious file can at worst kill its
own child process, never the API server.
"""

import json
import os
import subprocess
import sys
from dataclasses import dataclass, field

from ...config import get_settings
from .parsers import ExtractionError, detect_type


@dataclass
class ExtractionResult:
    text: str
    file_type: str
    pages: int | None = None
    warnings: list[str] = field(default_factory=list)


def _limits(memory_mb: int, cpu_s: int):
    def apply() -> None:  # runs in the child before exec
        import resource

        mem = memory_mb * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (mem, mem))
        resource.setrlimit(resource.RLIMIT_CPU, (cpu_s, cpu_s + 5))
        resource.setrlimit(resource.RLIMIT_FSIZE, (0, 0))  # child may not write files
        os.setsid()

    return apply


def extract(filename: str, data: bytes) -> ExtractionResult:
    settings = get_settings()
    if not data:
        raise ExtractionError("The file is empty.")
    if len(data) > settings.max_upload_mb * 1024 * 1024:
        raise ExtractionError(f"The file is larger than the {settings.max_upload_mb} MB limit.")
    file_type = detect_type(filename, data)

    backend_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    # -I (isolated: no cwd/user-site on sys.path, PYTHON* env ignored) and -B
    # (never write .pyc: the child has a zero file-size limit).
    bootstrap = (
        "import sys; sys.path.insert(0, sys.argv[1]); "
        "from app.services.extraction.child import main; sys.exit(main(sys.argv[2:]))"
    )
    cmd = [sys.executable, "-I", "-B", "-c", bootstrap, backend_root, file_type, str(settings.max_pdf_pages)]
    env = {"PATH": os.environ.get("PATH", ""), "LANG": "C.UTF-8"}
    preexec = _limits(settings.extraction_memory_mb, settings.extraction_timeout_s) if os.name == "posix" else None
    try:
        proc = subprocess.run(
            cmd,
            input=data,
            capture_output=True,
            timeout=settings.extraction_timeout_s,
            env=env,
            cwd=backend_root,
            preexec_fn=preexec,  # noqa: PLW1509 - required for rlimits
        )
    except subprocess.TimeoutExpired as e:
        raise ExtractionError("Reading the file took too long. It may be very large or malformed.") from e

    if proc.returncode != 0:
        try:
            payload = json.loads(proc.stdout or b"{}")
        except json.JSONDecodeError:
            payload = {}
        if payload.get("error"):
            raise ExtractionError(payload["error"])
        raise ExtractionError("The file could not be processed safely. It may be damaged or too complex.")
    payload = json.loads(proc.stdout)
    return ExtractionResult(
        text=payload["text"], file_type=file_type, pages=payload.get("pages"), warnings=payload.get("warnings", [])
    )
