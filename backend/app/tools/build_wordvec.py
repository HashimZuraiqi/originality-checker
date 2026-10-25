"""Convert spaCy's en_core_web_md vectors for the `wordvec` provider:
`python -m app.tools.build_wordvec`."""

from app.services.similarity.embeddings import WORDVEC_DIR, build_wordvec_files

if __name__ == "__main__":
    build_wordvec_files(WORDVEC_DIR)
    print(f"Word vectors written to {WORDVEC_DIR}")
