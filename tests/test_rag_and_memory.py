import json
from pathlib import Path

import numpy as np
import pytest

from backend.memory import DialogMemory
from backend.privacy import public_contacts_in, redact
from backend.rag_index import (build_index, load_faq_data, load_index, load_txt_documents, save_index, search,
                               search_entries)
from tests.fakes import ITEMS, FakeEmbedder


def test_faq_requires_question_and_answer(tmp_path: Path):
    ok = tmp_path / "faqs.json"
    ok.write_text(json.dumps([{"question": "Вопрос?", "answer": "Ответ."}], ensure_ascii=False), encoding="utf-8")
    assert load_faq_data(ok) == [{"question": "Вопрос?", "answer": "Ответ.", "source": "faqs.json#1"}]
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps([{"question1": "Вопрос?", "answer": "Ответ."}], ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ValueError, match="question и answer"):
        load_faq_data(bad)


def test_documents_split_by_sections_and_plain_file(tmp_path: Path):
    (tmp_path / "a.md").write_text("# Услуги\n\n## Кому помогает\nАгентствам.\n\n## Польза\nНе перепечатывать.\n",
                                   encoding="utf-8")
    (tmp_path / "b.txt").write_text("Контакты\n------\nTelegram @alexandero_optimum\n", encoding="utf-8")
    (tmp_path / "skip.json").write_text("{}", encoding="utf-8")
    docs = load_txt_documents(tmp_path)
    assert [d["question"] for d in docs] == ["Услуги: Кому помогает", "Услуги: Польза", "Контакты"]
    assert docs[1]["source"] == "a.md#Польза"
    assert "@alexandero_optimum" in docs[2]["answer"]


def test_search_threshold_merge_and_roundtrip(tmp_path: Path):
    emb = FakeEmbedder()
    vectors = emb.embed([f"{i['question']}\n{i['answer']}" for i in ITEMS])
    index = build_index(vectors)
    hits = search(index, ITEMS, emb.embed(["Сколько стоит настроить помощника для агентства?"]), k=3, min_score=0.3)
    assert hits and hits[0].item["source"] == "faqs.json#1"
    assert all(h.score >= 0.3 for h in hits)
    assert search(index, ITEMS, emb.embed(["zzzz qqqq"]), k=3, min_score=0.3) == []
    # Два запроса: у фрагмента берётся лучшая оценка, повторов нет.
    two = search(index, ITEMS, emb.embed(["Сколько стоит?", "Сколько стоит настроить помощника?"]), k=3, min_score=0.1)
    assert len({h.item["source"] for h in two}) == len(two)

    save_index(index, ITEMS, [0, 1, 2], tmp_path / "faiss.index", tmp_path / "meta.json", {"kb_version": "v1"})
    index2, items2, owners2, info = load_index(tmp_path / "faiss.index", tmp_path / "meta.json")
    assert items2 == ITEMS and owners2 == [0, 1, 2] and info["kb_version"] == "v1" and index2.ntotal == 3
    meta = json.loads((tmp_path / "meta.json").read_text(encoding="utf-8"))
    meta["owners"] = meta["owners"][:2]
    (tmp_path / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    with pytest.raises(RuntimeError, match="разошлись"):
        load_index(tmp_path / "faiss.index", tmp_path / "meta.json")


def test_long_section_is_searched_by_paragraphs_but_returned_whole():
    case = {"question": "Кейсы: объявления из чатов",
            "answer": ("Задача: сотрудник заводил объекты на сайте вручную, заполняя в админке все поля. " * 3
                       + "\n\nРешение: настроено автоматическое чтение 8 тематических групп и каналов Telegram."
                       + "\n\n" + "Результат: за октябрь помощник завёл 164 новых черновика из объявлений. " * 2),
            "source": "03-keisy.md#Кейс 1"}
    items = [*ITEMS, case]
    texts, owners = search_entries(items)
    assert len(texts) == len(ITEMS) + 1 + 3 and owners[-3:] == [3, 3, 3]  # раздел и три его абзаца
    assert all(t.startswith("Кейсы: объявления из чатов\n") for t in texts[-3:])  # абзац ищется с заголовком
    emb = FakeEmbedder()
    index = build_index(emb.embed(texts))
    hits = search(index, items, emb.embed(["сколько тематических групп и каналов Telegram читает"]), k=3,
                  min_score=0.1, owners=owners)
    assert hits[0].item is case and "164" in hits[0].item["answer"]  # найден абзац, отдан весь раздел
    assert [h.item["source"] for h in hits].count("03-keisy.md#Кейс 1") == 1  # без повторов
    assert search_entries(ITEMS)[1] == [0, 1, 2]  # короткие FAQ не режутся


def test_public_contacts_from_kb_survive_redaction():
    keep = public_contacts_in(["Бот @ZCoderTest_bot отвечает по базе. Почта alexandero.mobile@gmail.com"])
    text = redact("Что за бот @ZCoderTest_bot и t.me/zcodertest_bot? Мой ник @ivan_petrov, почта ivan@mail.ru",
                  keep=keep)
    assert "@ZCoderTest_bot" in text and "t.me/zcodertest_bot" in text  # ник бота из базы и ссылка на него
    assert "@ivan_petrov" not in text and "ivan@mail.ru" not in text


def test_old_journal_file_gets_new_column(tmp_path):
    import sqlite3

    from backend.journal import Journal, Record

    path = tmp_path / "journal.db"
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE interactions (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, session TEXT NOT NULL, "
                "status TEXT NOT NULL, reason TEXT NOT NULL DEFAULT '', query TEXT NOT NULL DEFAULT '', "
                "answer TEXT NOT NULL DEFAULT '', sources TEXT NOT NULL DEFAULT '', top_score REAL, "
                "history_turns INTEGER NOT NULL DEFAULT 0, retrieval_ms INTEGER, llm_ms INTEGER, total_ms INTEGER, "
                "prompt_tokens INTEGER NOT NULL DEFAULT 0, completion_tokens INTEGER NOT NULL DEFAULT 0, "
                "model TEXT NOT NULL DEFAULT '', kb_version TEXT NOT NULL DEFAULT '')")
    con.execute("INSERT INTO interactions (ts, session, status) VALUES ('2026-11-08T05:31:00', 'old', 'answered')")
    con.commit()
    con.close()
    journal = Journal(path)
    journal.write(Record(session="new", status="answered", search_query="Сколько стоит бот подбора квартир?"))
    rows = sqlite3.connect(path).execute("SELECT session, search_query FROM interactions ORDER BY id").fetchall()
    assert rows == [("old", ""), ("new", "Сколько стоит бот подбора квартир?")]


def test_memory_limits_ttl_and_eviction():
    now = [0.0]
    mem = DialogMemory(turns=2, ttl_seconds=60, max_sessions=2, clock=lambda: now[0])
    for n in range(3):
        mem.add("a" * 16, f"q{n}", f"a{n}")
    assert mem.history("a" * 16) == [("q1", "a1"), ("q2", "a2")]  # помнит только 2 последних пары
    mem.add("b" * 16, "q", "a")
    mem.add("c" * 16, "q", "a")  # третий диалог вытесняет самый старый
    assert mem.history("a" * 16) == [] and len(mem) == 2
    now[0] = 61.0
    assert mem.history("b" * 16) == []  # молчал дольше ttl — забыт
    mem.add("d" * 16, "q", "a")
    mem.reset("d" * 16)
    assert mem.history("d" * 16) == []


def test_normalized_vectors_give_cosine():
    index = build_index(np.array([[3.0, 4.0], [1.0, 0.0]], dtype="float32"))
    hits = search(index, [{"question": "x", "answer": "", "source": "x"}, {"question": "y", "answer": "", "source": "y"}],
                  np.array([[6.0, 8.0]], dtype="float32"), k=2, min_score=-1)
    assert hits[0].item["source"] == "x" and abs(hits[0].score - 1.0) < 1e-5
    assert abs(hits[1].score - 0.6) < 1e-5


def test_cleanup_removes_only_old_records(tmp_path, monkeypatch):
    from backend.cleanup import main as cleanup_main
    from backend.journal import Journal, Record

    monkeypatch.setenv("STATE_DIR", str(tmp_path))
    monkeypatch.setenv("RETENTION_DAYS", "90")
    journal = Journal(tmp_path / "journal.db")
    journal.write(Record(session="old", status="answered", ts="2020-01-01T00:00:00"))
    journal.write(Record(session="new", status="answered"))
    assert cleanup_main() == 1
    assert journal.stats()["total"] == 1


def test_assistant_refuses_index_built_by_other_model(tmp_path, monkeypatch):
    from backend.config import load_settings
    from backend.factory import build_assistant

    emb = FakeEmbedder()
    save_index(build_index(emb.embed(["a", "b", "c"])), ITEMS, [0, 1, 2], tmp_path / "faiss.index",
               tmp_path / "meta.json", {"embed_model": "text-embedding-3-small"})
    monkeypatch.setenv("INDEX_DIR", str(tmp_path))
    monkeypatch.setenv("STATE_DIR", str(tmp_path))
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-used")
    monkeypatch.setenv("USER_HASH_SALT", "test-salt")
    monkeypatch.setenv("EMBED_MODEL", "text-embedding-3-large")
    with pytest.raises(RuntimeError, match="пересоберите индекс"):
        build_assistant(load_settings())


def test_shipped_index_matches_data_files():
    """Индекс в data/index собран из нынешних faqs.json и docs: правку базы без пересборки видно сразу."""
    from backend.rag_index import kb_version

    root = Path(__file__).resolve().parents[1] / "data"
    meta = json.loads((root / "index" / "meta.json").read_text(encoding="utf-8"))
    items = load_faq_data(root / "faqs.json") + load_txt_documents(root / "docs")
    assert items == meta["items"], "базу знаний поменяли, а индекс не пересобрали: python -m backend.build_index"
    assert kb_version(items, meta["embed_model"]) == meta["kb_version"]


def test_redaction_edge_cases():
    keep = ("@alexandero_optimum",)
    assert redact("звоните 8.900.123.45.67", keep=keep) == "звоните [номер]"
    assert redact("созвон 08.11.2026 12:00", keep=keep) == "созвон 08.11.2026 12:00"  # дата — не номер
    assert redact("Меня зовут John Smith", keep=keep) == "Меня зовут [имя]"
    assert redact("тг@ivan_petrov", keep=keep) == "тг[ник]"
    assert redact("бюджет 1.500.000 руб", keep=keep) == "бюджет 1.500.000 руб"
    assert redact("пишите @alexandero_optimum", keep=keep) == "пишите @alexandero_optimum"
