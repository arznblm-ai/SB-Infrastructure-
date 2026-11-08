import task_review as review

def test_parse_result_with_surrounding_prose():
    assert review.parse_result('Готово:\n```json\n{"decisions": [], "notes": "ok"}\n```')['notes'] == 'ok'

def test_completion_cannot_be_inferred_from_related_event():
    assert not review.explicit_completion({'quote': 'суббота 12:00, общий календарь'})
    assert review.explicit_completion({'quote': '02.11 Антон: проект КИТ сдали'})

def test_evidence_must_be_verbatim_dated_and_in_meetings(tmp_path, monkeypatch):
    monkeypatch.setattr(review, 'VAULT', tmp_path)
    meetings = tmp_path / 'meetings'; meetings.mkdir()
    source = meetings / 'summary 2026-11-06.md'
    source.write_text('Антон отправил команде готовую презентацию.', encoding='utf-8')
    task = {'created_at': '2026-11-05T10:00:00Z'}
    item = {'source': 'meetings/summary 2026-11-06.md', 'quote': 'Антон отправил команде готовую презентацию.'}
    assert review.valid_evidence(item, task)
    assert not review.valid_evidence({**item, 'quote': 'Выдуманное доказательство исполнения задачи'}, task)
    assert not review.valid_evidence(item, {'created_at': '2026-11-07T00:00:00Z'})
    assert not review.valid_evidence({**item, 'source': '../outside.md'}, task)
    assert not review.valid_evidence(item, {})

def test_kris_project_status_requires_dated_quote_and_excludes_private_subfolders(tmp_path, monkeypatch):
    monkeypatch.setattr(review, 'VAULT', tmp_path)
    kris = tmp_path / 'infrastructure/Kris/memory'; kris.mkdir(parents=True)
    (kris / 'status.md').write_text('02.11 · Антон: проект КИТ сдан и снят с контроля.', encoding='utf-8')
    task = {'created_at': '2026-10-22T10:00:00Z'}
    item = {'source': 'infrastructure/Kris/memory/status.md', 'date': '2026-11-02', 'quote': '02.11 · Антон: проект КИТ сдан и снят с контроля.'}
    assert review.valid_evidence(item, task)
    assert not review.valid_evidence({**item, 'date': '2026-11-03'}, task)
    private = kris / 'estimates'; private.mkdir()
    (private / 'status.md').write_text(item['quote'], encoding='utf-8')
    assert not review.valid_evidence({**item, 'source': 'infrastructure/Kris/memory/estimates/status.md'}, task)
