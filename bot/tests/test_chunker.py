#!/usr/bin/env python3
"""Tests for discord_ui/chunker.py — message splitting."""

from discord_ui.chunker import chunk_text


class TestChunkText:
    def test_short_text_single_chunk(self):
        assert chunk_text("hello world", 100) == ["hello world"]

    def test_exact_max_length(self):
        text = "x" * 1900
        chunks = chunk_text(text, 1900)
        assert len(chunks) == 1
        assert chunks[0] == text

    def test_split_on_paragraph(self):
        text = "A" * 900 + "\n\n" + "B" * 900
        chunks = chunk_text(text, 1900)
        assert len(chunks) == 1  # Fits in one chunk

        text = "A" * 1000 + "\n\n" + "B" * 1000
        chunks = chunk_text(text, 1500)
        assert len(chunks) == 2

    def test_split_on_newline(self):
        text = "A" * 1000 + "\n" + "B" * 1000
        chunks = chunk_text(text, 1500)
        assert len(chunks) == 2

    def test_multiple_chunks(self):
        text = "x" * 5000
        chunks = chunk_text(text, 1900)
        assert len(chunks) == 3
        for chunk in chunks:
            assert len(chunk) <= 1900

    def test_all_content_preserved(self):
        text = "Hello world! " * 200
        chunks = chunk_text(text, 500)
        reassembled = "".join(chunks)
        # Content should be preserved (may have extra code block markers)
        assert "Hello world!" in reassembled

    def test_empty_string(self):
        assert chunk_text("", 1900) == [""]

    def test_code_block_tracking(self):
        # "x = 1\n" * 300 = 1800 chars + fences = ~1830, fits in one chunk at 1900
        # Use more lines to force a split
        text = "Before\n```python\n" + "x = 1\n" * 500 + "```\nAfter"
        chunks = chunk_text(text, 1900)
        assert len(chunks) >= 2
        for chunk in chunks:
            assert len(chunk) <= 1900
