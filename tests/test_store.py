from mytts.contracts import Book, Chapter, JobSettings, JobStatus, Lang, Segment
from mytts.pipeline.store import audible_settings_hash, sanitize_filename


def _book() -> Book:
    return Book(
        title="Test Book", author="A. Author", lang=Lang.en, source_path="/tmp/x.txt",
        source_format="txt", cover=b"\xff\xd8\xff", cover_mime="image/jpeg",
        chapters=[
            Chapter(index=0, title="One", paragraphs=["Hello there.", "Second para."]),
            Chapter(index=1, title="Two", paragraphs=["More text."], include=False, kind="notes"),
        ],
    )


def test_create_and_read_job_roundtrip(store, tmp_path):
    src = tmp_path / "book.txt"
    src.write_text("hello")
    job_id = store.create_job(_book(), JobSettings(), src)
    info = store.get_job_info(job_id)
    assert info is not None
    assert info.status == JobStatus.parsed
    assert info.title == "Test Book"
    assert info.has_cover is True
    assert len(info.chapters) == 2
    assert info.chapters[1].include is False

    book_json = store.read_book_json(job_id)
    assert book_json["cover"]  # base64 present
    assert (store.job_workdir(job_id) / "upload.txt").is_file()


def test_update_chapters_meta(store, tmp_path):
    src = tmp_path / "book.txt"
    src.write_text("hello")
    job_id = store.create_job(_book(), JobSettings(), src)
    store.update_chapters_meta(job_id, [{"index": 1, "include": True, "title": "Renamed"}])
    chapters = store.get_chapters(job_id)
    assert chapters[1].include is True
    assert chapters[1].title == "Renamed"


def test_segments_pending_order_and_lifecycle(store, tmp_path):
    src = tmp_path / "book.txt"
    src.write_text("hello")
    job_id = store.create_job(_book(), JobSettings(), src)
    segs = [
        Segment(id="c000s0000", chapter=0, index=0, source="a", text="a", lang=Lang.en,
                pause_after_ms=100),
        Segment(id="c000s0001", chapter=0, index=1, source="b", text="b", lang=Lang.en,
                pause_after_ms=100),
    ]
    store.add_segments(job_id, 0, segs)
    pending = store.next_pending_segments(job_id, 10)
    assert [r["id"] for r in pending] == ["c000s0000", "c000s0001"]

    store.mark_segment_running(job_id, "c000s0000")
    row = store.get_segment(job_id, "c000s0000")
    assert row["status"] == "running" and row["attempts"] == 1

    store.mark_segment_done(job_id, "c000s0000", "/tmp/out.wav", 1.5, 0.0, "a")
    row = store.get_segment(job_id, "c000s0000")
    assert row["status"] == "done" and row["duration_s"] == 1.5

    store.mark_segment_failed(job_id, "c000s0001", "boom")
    row = store.get_segment(job_id, "c000s0001")
    assert row["status"] == "failed" and row["error"] == "boom"

    totals = store.job_segment_totals(job_id)
    assert totals == {"done": 1, "failed": 1}
    assert store.next_pending_segments(job_id, 10) == []


def test_reset_running_segments(store, tmp_path):
    src = tmp_path / "book.txt"
    src.write_text("hello")
    job_id = store.create_job(_book(), JobSettings(), src)
    seg = Segment(id="c000s0000", chapter=0, index=0, source="a", text="a", lang=Lang.en,
                 pause_after_ms=0)
    store.add_segments(job_id, 0, [seg])
    store.mark_segment_running(job_id, "c000s0000")
    store.reset_running_segments(job_id)
    assert store.get_segment(job_id, "c000s0000")["status"] == "pending"


def test_delete_job_removes_workdir(store, tmp_path):
    src = tmp_path / "book.txt"
    src.write_text("hello")
    job_id = store.create_job(_book(), JobSettings(), src)
    workdir = store.job_workdir(job_id)
    assert workdir.is_dir()
    store.delete_job(job_id)
    assert store.get_job_info(job_id) is None
    assert not workdir.is_dir()


def test_audible_settings_hash_changes_on_voice_but_not_output_dir():
    a = JobSettings(voice_id="ru_male", output_dir="/a")
    b = JobSettings(voice_id="ru_male", output_dir="/b")
    c = JobSettings(voice_id="en_male", output_dir="/a")
    assert audible_settings_hash(a) == audible_settings_hash(b)
    assert audible_settings_hash(a) != audible_settings_hash(c)


def test_sanitize_filename_keeps_cyrillic():
    name = sanitize_filename("Толстой Л.Н. - Война и мир: Книга 1?")
    assert "Толстой" in name and "?" not in name and "/" not in name


def test_sample_lifecycle(store, tmp_path):
    src = tmp_path / "book.txt"
    src.write_text("hello")
    job_id = store.create_job(_book(), JobSettings(), src)
    segs = [Segment(id="smp_x-0000", chapter=0, index=0, source="a", text="a", lang=Lang.en,
                    pause_after_ms=0)]
    store.create_sample("smp_x", job_id, JobSettings(), "a", segs)
    srow = store.get_sample("smp_x")
    assert srow["job_id"] == job_id and srow["status"] == "queued"
    oldest = store.oldest_pending_sample()
    assert oldest["id"] == "smp_x"
    pending = store.next_pending_sample_segments_for("smp_x", 10)
    assert len(pending) == 1
    store.mark_sample_segment_done("smp_x", "smp_x-0000", "/tmp/s.wav", 0.5)
    assert store.sample_segments_pending_count("smp_x") == 0
    assert store.oldest_pending_sample() is None
