#!/usr/bin/env python3
"""
chunk_for_rag.py
=================
Turns the crawler's output/ folder into RAG-ready chunks: output/chunks.jsonl.

Reads manifest.jsonl (the single source of truth for what's "ok" and where
its content lives) and chunks:
  - pages/*.md          (crawled HTML pages, markdown)
  - files/*.txt         (parsed PDF/DOCX/PPTX/XLSX/CSV text)
  - */*.tables/*.csv     (tables already extracted + de-junked by the crawler)

Non-LLM, hand-rolled recursive splitter (same core algorithm as
LangChain's RecursiveCharacterTextSplitter: try paragraph breaks, fall back
to line breaks, then sentences, then hard character cuts) plus tiktoken for
accurate token-based sizing. See the conversation for why this wasn't built
on top of langchain-text-splitters — table/page-marker handling and the
manifest-driven orchestration are bespoke either way, and the splitter
itself is small enough not to need it.

Chunking strategy
------------------
- Markdown pages: split on heading structure first (a chunk never silently
  crosses from one section into an unrelated one), then pack paragraphs up
  to --chunk-tokens with --overlap-tokens overlap. Every chunk is prefixed
  with its heading breadcrumb so it reads sensibly out of context.
- PDFs/PPTX text: pre-segmented on the "--- Page N ---" / "--- Slide N ---"
  markers already present in the extracted text (see nust_crawler.py's
  parse_pdf/parse_pptx), then packed the same way. Breadcrumb = "Page 3" /
  "Slide 5".
- DOCX/plain text: no natural section markers, so packed directly.
- Tables (CSV): kept intact as a single chunk when reasonably sized. Large
  tables are split into row-batches with the header row repeated in every
  batch, so no chunk ever has a data row without its column meaning.
- Duplicate pages/files (crawler already flagged these via content-hash
  dedup) and OCR-needed / zero-text PDFs are skipped, not chunked as
  empty noise.

Usage
-----
    pip install tiktoken
    python chunk_for_rag.py --output output
    python chunk_for_rag.py --output output --chunk-tokens 400 --overlap-tokens 60
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from pathlib import Path

try:
    import tiktoken
    try:
        _ENC = tiktoken.get_encoding("cl100k_base")

        def count_tokens(text: str) -> int:
            return len(_ENC.encode(text, disallowed_special=()))
    except Exception:
        # tiktoken downloads its vocab file from openaipublic's blob storage
        # on first use — fails in offline/firewalled environments even
        # though the package itself imported fine. Fall back rather than
        # crash the whole run over a token-counting nicety.
        print(
            "Warning: tiktoken installed but its vocab file couldn't be "
            "fetched (no/blocked network access) — falling back to an "
            "approximate ~4 chars/token estimate for chunk sizing."
        )

        def count_tokens(text: str) -> int:
            return max(1, len(text) // 4)
except ImportError:
    # ~4 chars/token is the standard rough estimate for English text.
    def count_tokens(text: str) -> int:
        return max(1, len(text) // 4)


# =========================================================================
# Section splitting (heading-aware / page-marker-aware)
# =========================================================================

HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
PAGE_SLIDE_MARKER_RE = re.compile(r"^---\s*(Page \d+|Slide \d+)\s*---$")
FRONTMATTER_RE = re.compile(r"^---\n.*?\n---\n\n?", re.DOTALL)


def strip_frontmatter(text: str) -> str:
    return FRONTMATTER_RE.sub("", text, count=1)


def split_markdown_sections(md_text: str) -> list[tuple[list[str], str]]:
    """Returns [(heading_breadcrumb, body_text), ...]."""
    lines = md_text.split("\n")
    sections: list[tuple[list[str], str]] = []
    stack: list[tuple[int, str]] = []
    current_body: list[str] = []

    def flush():
        body = "\n".join(current_body).strip()
        if body:
            sections.append(([t for _, t in stack], body))

    for line in lines:
        m = HEADING_RE.match(line)
        if m:
            flush()
            current_body = []
            level = len(m.group(1))
            stack = [s for s in stack if s[0] < level]
            stack.append((level, m.group(2).strip()))
        else:
            current_body.append(line)
    flush()

    return sections or [([], md_text.strip())]


def split_marker_sections(text: str) -> list[tuple[list[str], str]]:
    """Same idea as split_markdown_sections, but for the '--- Page N ---'
    / '--- Slide N ---' markers parse_pdf/parse_pptx already insert."""
    lines = text.split("\n")
    sections: list[tuple[list[str], str]] = []
    label: str | None = None
    current_body: list[str] = []

    def flush():
        body = "\n".join(current_body).strip()
        if body:
            sections.append(([label] if label else [], body))

    for line in lines:
        m = PAGE_SLIDE_MARKER_RE.match(line.strip())
        if m:
            flush()
            current_body = []
            label = m.group(1)
        else:
            current_body.append(line)
    flush()

    return sections or [([], text.strip())]


# =========================================================================
# Recursive paragraph/sentence packer (the actual "splitter" part)
# =========================================================================

SENTENCE_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9])")


def split_long_paragraph(paragraph: str, max_tokens: int) -> list[str]:
    """Fallback for a single paragraph that alone exceeds the chunk budget:
    try sentence boundaries first, then hard character cuts as a last
    resort (e.g. a paragraph with no sentence punctuation at all)."""
    sentences = [s for s in SENTENCE_RE.split(paragraph) if s.strip()]
    if len(sentences) <= 1:
        sentences = [paragraph]

    pieces: list[str] = []
    current = ""
    for s in sentences:
        candidate = f"{current} {s}".strip() if current else s
        if current and count_tokens(candidate) > max_tokens:
            pieces.append(current)
            current = s
        else:
            current = candidate
    if current:
        pieces.append(current)

    # last-resort hard cut for any piece that's still oversized (e.g. one
    # giant "sentence" with no punctuation)
    final: list[str] = []
    for p in pieces:
        if count_tokens(p) <= max_tokens * 1.5:
            final.append(p)
        else:
            words = p.split()
            buf = []
            for w in words:
                buf.append(w)
                if count_tokens(" ".join(buf)) >= max_tokens:
                    final.append(" ".join(buf))
                    buf = []
            if buf:
                final.append(" ".join(buf))
    return final


def pack_paragraphs(body: str, max_tokens: int, overlap_tokens: int) -> list[str]:
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", body) if p.strip()]

    expanded: list[str] = []
    for p in paragraphs:
        if count_tokens(p) <= max_tokens:
            expanded.append(p)
        else:
            expanded.extend(split_long_paragraph(p, max_tokens))

    chunks: list[str] = []
    current = ""
    for p in expanded:
        candidate = f"{current}\n\n{p}" if current else p
        if current and count_tokens(candidate) > max_tokens:
            chunks.append(current)
            # sliding-window overlap: carry the tail of the previous chunk
            # (by tokens, not just characters) into the next one so a fact
            # split across the boundary isn't lost to either side.
            if overlap_tokens > 0:
                tail_words = current.split()
                tail = ""
                for w in reversed(tail_words):
                    candidate_tail = f"{w} {tail}".strip()
                    if count_tokens(candidate_tail) > overlap_tokens:
                        break
                    tail = candidate_tail
                current = f"{tail}\n\n{p}".strip() if tail else p
            else:
                current = p
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks


def breadcrumb_str(parts: list[str], title: str | None = None) -> str:
    bc = [p for p in parts if p]
    if title:
        bc = [title] + bc
    return " > ".join(bc)


def chunk_text_document(
    text: str,
    doc_kind: str,  # "markdown" | "marker" | "plain"
    title: str,
    max_tokens: int,
    overlap_tokens: int,
) -> list[dict]:
    if doc_kind == "markdown":
        sections = split_markdown_sections(text)
    elif doc_kind == "marker":
        sections = split_marker_sections(text)
    else:
        sections = [([], text.strip())]

    out = []
    for heading_path, body in sections:
        for piece in pack_paragraphs(body, max_tokens, overlap_tokens):
            bc = breadcrumb_str(heading_path, title)
            full_text = f"{bc}\n\n{piece}" if bc else piece
            out.append({"text": full_text, "breadcrumb": bc, "heading_path": heading_path})
    return out


# =========================================================================
# Table chunking
# =========================================================================


def chunk_table_csv(csv_path: Path, has_headers: bool, max_rows_per_chunk: int = 40) -> list[str]:
    with csv_path.open(newline="", encoding="utf-8") as f:
        rows = list(csv.reader(f))
    if not rows:
        return []
    header, data_rows = (rows[0], rows[1:]) if has_headers else (None, rows)
    if not data_rows and not header:
        return []
    batches = [data_rows[i : i + max_rows_per_chunk] for i in range(0, len(data_rows), max_rows_per_chunk)]
    if not batches:
        batches = [[]]

    chunks = []
    for batch in batches:
        lines = []
        if header:
            lines.append(" | ".join(header))
            lines.append(" | ".join(["---"] * len(header)))
        for row in batch:
            lines.append(" | ".join(row))
        chunks.append("\n".join(lines))
    return chunks


# =========================================================================
# Main driver
# =========================================================================


def make_chunk_id(source_url: str, kind: str, index: int) -> str:
    return hashlib.sha256(f"{source_url}::{kind}::{index}".encode()).hexdigest()[:24]


def process_record(rec: dict, out_dir: Path, max_tokens: int, overlap_tokens: int) -> list[dict]:
    if rec.get("status") != "ok" or rec.get("duplicate_of"):
        return []
    if rec.get("parsed") is False:  # file downloaded but couldn't be parsed to text
        return []
    if rec.get("needs_ocr"):
        return []

    url = rec["url"]
    title = rec.get("title") or ""
    chunks: list[dict] = []

    # ---- text content ----------------------------------------------------
    text_rel = rec.get("content_path") or rec.get("text_path")
    if text_rel:
        text_path = out_dir / text_rel
        if text_path.exists():
            raw = text_path.read_text(encoding="utf-8")
            if rec["type"] == "page":
                raw = strip_frontmatter(raw)
                doc_kind = "markdown"
            elif rec.get("extension") in (".pdf", ".pptx"):
                doc_kind = "marker"
            else:
                doc_kind = "plain"

            pieces = chunk_text_document(raw, doc_kind, title, max_tokens, overlap_tokens)
            for i, piece in enumerate(pieces):
                chunks.append(
                    {
                        "chunk_id": make_chunk_id(url, "text", i),
                        "source_url": url,
                        "source_type": rec["type"],
                        "content_kind": "text",
                        "title": title,
                        "chunk_index": i,
                        "n_chunks_from_source_text": len(pieces),
                        "breadcrumb": piece["breadcrumb"],
                        "text": piece["text"],
                        "approx_tokens": count_tokens(piece["text"]),
                        "extension": rec.get("extension"),
                        "depth": rec.get("depth"),
                        "fetched_at": rec.get("fetched_at"),
                    }
                )

    # ---- tables -------------------------------------------------------
    tables = rec.get("tables") or []
    for t_idx, t in enumerate(tables):
        csv_rel = t.get("path")
        if not csv_rel:
            continue
        csv_path = out_dir / csv_rel
        if not csv_path.exists():
            continue
        table_batches = chunk_table_csv(csv_path, has_headers=t.get("has_headers", False))
        loc_label = None
        for key in ("page", "sheet", "slide"):
            if t.get(key) is not None:
                loc_label = f"{key.capitalize()} {t[key]}"
                break
        for b_idx, batch_text in enumerate(table_batches):
            bc = breadcrumb_str([f"Table {t_idx}" + (f" ({loc_label})" if loc_label else "")], title)
            chunks.append(
                {
                    "chunk_id": make_chunk_id(url, f"table{t_idx}", b_idx),
                    "source_url": url,
                    "source_type": rec["type"],
                    "content_kind": "table",
                    "title": title,
                    "chunk_index": b_idx,
                    "n_chunks_from_source_text": len(table_batches),
                    "breadcrumb": bc,
                    "text": f"{bc}\n\n{batch_text}" if bc else batch_text,
                    "approx_tokens": count_tokens(batch_text),
                    "table_index": t_idx,
                    "table_row_batch": b_idx,
                    "extension": rec.get("extension"),
                    "depth": rec.get("depth"),
                    "fetched_at": rec.get("fetched_at"),
                }
            )

    return chunks


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--output", default="output", help="Crawler output directory.")
    ap.add_argument("--chunk-tokens", type=int, default=400, help="Target chunk size, in tokens.")
    ap.add_argument("--overlap-tokens", type=int, default=60, help="Sliding-window overlap, in tokens.")
    ap.add_argument("--out-file", default=None, help="Output path (default: <output>/chunks.jsonl).")
    args = ap.parse_args()

    out_dir = Path(args.output)
    manifest_path = out_dir / "manifest.jsonl"
    if not manifest_path.exists():
        raise SystemExit(f"No manifest found at {manifest_path}")

    out_path = Path(args.out_file) if args.out_file else out_dir / "chunks.jsonl"

    n_sources = 0
    n_chunks = 0
    n_skipped = 0

    with manifest_path.open("r", encoding="utf-8") as f, out_path.open("w", encoding="utf-8") as out_f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue

            chunks = process_record(rec, out_dir, args.chunk_tokens, args.overlap_tokens)
            if not chunks:
                if rec.get("status") == "ok":
                    n_skipped += 1
                continue

            n_sources += 1
            for c in chunks:
                out_f.write(json.dumps(c, ensure_ascii=False) + "\n")
                n_chunks += 1

    print(f"Sources chunked:        {n_sources}")
    print(f"Sources skipped (dup/OCR/unparsed/empty): {n_skipped}")
    print(f"Total chunks written:   {n_chunks}")
    print(f"Output: {out_path}")


if __name__ == "__main__":
    main()
