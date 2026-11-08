"""Общие фикстуры: ассистент на подменах без сети."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.assistant import Assistant  # noqa: E402
from backend.journal import Journal  # noqa: E402
from backend.memory import DialogMemory  # noqa: E402
from backend.rag_index import build_index, item_text  # noqa: E402
from tests.fakes import ITEMS, FakeChat, FakeEmbedder  # noqa: E402


@pytest.fixture
def embedder():
    return FakeEmbedder()


@pytest.fixture
def chat():
    return FakeChat()


@pytest.fixture
def assistant(tmp_path, embedder, chat):
    vectors = embedder.embed([item_text(i) for i in ITEMS])
    embedder.calls.clear()
    return Assistant(embedder=embedder, chat=chat, index=build_index(vectors), items=ITEMS,
                     memory=DialogMemory(turns=3, ttl_seconds=1800, max_sessions=100),
                     journal=Journal(tmp_path / "journal.db"), top_k=2, min_similarity=0.3,
                     salt="test-salt", public_contacts=("@alexandero_optimum",), kb_version="test")
