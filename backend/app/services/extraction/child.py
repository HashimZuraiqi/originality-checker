"""Entry point of the sandboxed extraction process.

Reads the file from stdin, writes {"text", "pages", "warnings"} or
{"error"} as JSON to stdout. Started by sandbox.py, which puts the backend
directory on sys.path itself because `python -I` ignores PYTHONPATH.
"""

import json
import sys

from app.services.extraction.parsers import ExtractionError, extract_docx, extract_pdf, extract_txt


def main(argv: list[str]) -> int:
    file_type, max_pages = argv[0], int(argv[1])
    data = sys.stdin.buffer.read()
    try:
        if file_type == "pdf":
            result = extract_pdf(data, max_pages)
        elif file_type == "docx":
            result = extract_docx(data)
        else:
            result = extract_txt(data)
    except ExtractionError as e:
        sys.stdout.write(json.dumps({"error": str(e)}))
        return 2
    except MemoryError:
        sys.stdout.write(json.dumps({"error": "The file needs too much memory to process safely."}))
        return 3
    sys.stdout.write(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
