import task_review as review

def test_parse_result_with_surrounding_prose():
    assert review.parse_result('Готово:\n```json\n{"decisions": [], "notes": "ok"}\n```')['notes'] == 'ok'

def test_reference_bundle_only_uses_approved_directories(tmp_path):
    meetings = tmp_path / 'meetings'; meetings.mkdir()
    (meetings / 'summary – 2026-11-01.md').write_text('meeting facts')
    (meetings / 'archive').mkdir()
    (meetings / 'archive' / 'old – 2026-11-01.md').write_text('secret archive')
    kris = tmp_path / 'infrastructure/Kris/memory'; kris.mkdir(parents=True)
    (kris / 'status.md').write_text('project status')
    (kris / 'estimates').mkdir()
    (kris / 'estimates' / 'private.md').write_text('secret estimate')
    bundle = review.reference_bundle(tmp_path)
    assert 'meeting facts' in bundle and 'project status' in bundle
    assert 'secret archive' not in bundle and 'secret estimate' not in bundle

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


def test_compact_context_preserves_exact_status_and_deduplicates_shared_evidence():
    source = 'infrastructure/Kris/memory/status.md'
    status = '02.11 · Антон: проект КИТ завершили и сдали заказчику.'
    payment = '02.11 · Оплата за КИТ ещё не поступила, ждём заказчика.'
    docs = [(source, f'{status}\n\n{payment}\n\n' + ('нерелевантный текст ' * 1200))]
    tasks = [{'title': 'КИТ: получить оплату'}, {'title': 'КИТ: проверить сдачу'}]

    bundle = review.compact_reference_bundle(docs, tasks, limit=350)

    assert len(bundle) <= 350
    assert bundle.count(status) == 1
    assert bundle.count(payment) == 1
    assert f'### ИСТОЧНИК: {source}\n{status}' in bundle


def test_compact_context_keeps_recent_meeting_decision_with_its_context():
    source = 'meetings/summary – 2026-11-14.md'
    body = '# Зекиро\n\nПолучили бриф.\n\n## Решения\n\nРешили запустить оформление сейчас.'
    bundle = review.compact_reference_bundle([(source, body)], [{'title': 'Оформление'}], limit=1000)
    assert body in bundle
    assert bundle.count(source) == 1
