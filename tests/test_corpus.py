from __future__ import annotations

from pathlib import Path

from cclc.core.corpus import Corpus, CorpusProject
from tests.conftest import write_eaf


def test_add_remove_no_duplicates():
    corpus = Corpus("A")
    assert corpus.add(Path("/x/a.eaf"))
    assert not corpus.add(Path("/x/a.eaf"))  # duplicate
    assert corpus.files == [Path("/x/a.eaf")]
    assert corpus.remove(Path("/x/a.eaf"))
    assert not corpus.remove(Path("/x/a.eaf"))


def test_add_corpus_unique_names():
    project = CorpusProject()
    c1 = project.add_corpus()
    c2 = project.add_corpus()
    assert c1.name != c2.name
    dup = project.add_corpus(c1.name)
    assert dup.name != c1.name


def test_rename_and_remove_corpus():
    project = CorpusProject()
    project.add_corpus("A")
    project.add_corpus("B")
    assert not project.rename_corpus("A", "B")  # name clash
    assert project.rename_corpus("A", "C")
    assert project.corpus_names() == ["C", "B"]
    assert project.remove_corpus("C")
    assert project.corpus_names() == ["B"]


def test_move_file_between_corpora():
    project = CorpusProject()
    project.add_corpus("A")
    project.add_corpus("B")
    p = Path("/x/a.eaf")
    project.add_file("A", p)
    assert project.move_file("A", "B", p)
    assert project.get_corpus("A").files == []
    assert project.get_corpus("B").files == [p]
    # moving a non-member fails
    assert not project.move_file("A", "B", Path("/x/missing.eaf"))


def test_move_file_same_corpus_is_noop_success():
    project = CorpusProject()
    project.add_corpus("A")
    p = Path("/x/a.eaf")
    project.add_file("A", p)
    assert project.move_file("A", "A", p)
    assert project.get_corpus("A").files == [p]


def test_json_round_trip(tmp_path):
    project = CorpusProject()
    project.add_corpus("Alpha")
    project.add_file("Alpha", Path("/data/one.eaf"))
    project.add_file("Alpha", Path("/data/two.eaf"))
    out = tmp_path / "proj.json"
    project.save(out)

    loaded = CorpusProject.load(out)
    assert loaded.corpus_names() == ["Alpha"]
    assert loaded.get_corpus("Alpha").files == [
        Path("/data/one.eaf"),
        Path("/data/two.eaf"),
    ]


def test_document_cache_reparses_on_change(tmp_path):
    path = write_eaf(tmp_path / "a.eaf", tiers={"T": [("x", 0, 10)]})
    project = CorpusProject()
    doc1 = project.document(path)
    doc2 = project.document(path)
    assert doc1 is doc2  # cached

    import os
    import time

    time.sleep(0.01)
    write_eaf(path, tiers={"T": [("y", 0, 10)]})
    os.utime(path, None)
    doc3 = project.document(path)
    assert doc3.tiers["T"].annotations[0].value == "y"


def test_missing_files(tmp_path):
    existing = write_eaf(tmp_path / "a.eaf", tiers={"T": [("x", 0, 10)]})
    project = CorpusProject()
    project.add_corpus("A")
    project.add_file("A", existing)
    project.add_file("A", tmp_path / "gone.eaf")
    assert project.missing_files() == [tmp_path / "gone.eaf"]
