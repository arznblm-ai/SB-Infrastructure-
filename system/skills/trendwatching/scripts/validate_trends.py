#!/usr/bin/env python3
"""Проверяет trends.json до генерации артефактов.

    python3 validate_trends.py trends.json          # проверить
    python3 validate_trends.py trends.json --strict # предупреждения тоже валят прогон

Смысл скрипта в том, чтобы правила скилла перестали быть пожеланиями.
Ошибка блокирует генерацию отчёта и радара; предупреждение печатается,
но пропускает — кроме режима --strict.

Главная проверка — доказательная база: тренд не поднимается выше
«зарождающегося» без первичного документа. Пересказы и агрегаторы
независимыми подтверждениями не считаются. Уровни T1–T4 и поправка для
рынков со слабой открытостью данных описаны в references/06-sources.md.
"""

import argparse
import os
import re
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import trendlib as T  # noqa: E402


def norm_title(text):
    return re.sub(r"[^a-zа-яё0-9 ]+", "", (text or "").lower()).strip()


class Report:
    def __init__(self):
        self.errors, self.warnings, self.notes = [], [], []

    def err(self, where, msg):
        self.errors.append((where, msg))

    def warn(self, where, msg):
        self.warnings.append((where, msg))

    def note(self, msg):
        self.notes.append(msg)


def check_signals(data, rep):
    seen = set()
    for s in data.get("signals", []):
        sid = s.get("id", "<без id>")
        if sid in seen:
            rep.err(sid, "дублирующийся id сигнала")
        seen.add(sid)

        if not s.get("date_event"):
            rep.err(sid, "нет date_event — сигнал без даты в работу не идёт")
        if not s.get("url"):
            rep.err(sid, "нет URL — сигнал нельзя проверить, значит нельзя использовать как доказательство")
        if not s.get("fact", "").strip():
            rep.err(sid, "пустое поле fact")
        elif not s["fact"].lstrip().startswith("[F]"):
            rep.warn(sid, "поле fact не помечено [F] — факт и интерпретация должны быть разделены явно")
        if s.get("interpretation") and not s.get("alternative"):
            rep.warn(sid, "есть интерпретация, но нет альтернативного объяснения")
        if s.get("reliability") not in {"A", "B", "C"}:
            rep.err(sid, "reliability должен быть A, B или C")
        head = (s.get("headline_fact") or "").lower()
        if re.search(r"\b(рынок переходит|тренд|становится нормой|все больше|всё больше)\b", head):
            rep.warn(sid, "заголовок похож на вывод, а не на факт: переформулируйте как наблюдение")

    titles = {}
    for s in data.get("signals", []):
        key = norm_title(s.get("headline_fact"))[:60]
        if key:
            titles.setdefault(key, []).append(s["id"])
    for ids in titles.values():
        if len(ids) > 1:
            rep.warn(", ".join(ids), "почти совпадающие заголовки — проверьте, не один ли это первоисточник")

    total = len(data.get("signals", []))
    if total:
        types = Counter(s.get("source_type") for s in data["signals"])
        top_type, top_n = types.most_common(1)[0]
        if top_n / total > 0.35:
            rep.warn("портфель", f"тип источника «{top_type}» даёт {top_n}/{total} сигналов "
                                 f"({top_n / total:.0%}) — порог 35%, портфель перекошен")
        doms = Counter(d for d in (T.domain(s.get("url")) for s in data["signals"]) if d)
        if doms:
            top_dom, dn = doms.most_common(1)[0]
            if dn / total > 0.30:
                rep.warn("портфель", f"домен {top_dom} даёт {dn}/{total} сигналов — проверьте независимость")
        if len(types) < 4:
            rep.warn("портфель", f"использовано {len(types)} типов источников; норматив — минимум 4 "
                                 f"для экспресс-прогона и 8 для полного")

        tiers = Counter(T.tier(s) for s in data["signals"])
        rep.note("Уровни доказательности: " + ", ".join(f"{k} — {tiers.get(k, 0)}" for k in ("T1", "T2", "T3", "T4")))
        if tiers.get("T1", 0) == 0:
            rep.err("портфель", "ни одного первичного документа (T1): нет ни НПА, ни отчётности, "
                                "ни исследования с раскрытой методикой. Доказательная база держится на пересказах")


def check_trends(data, rep, weight_table):
    index = T.signals_index(data)
    known = set(index)
    seen = set()
    valid_keys = {k for k, _, _, _ in weight_table}

    for tr in data.get("trends", []):
        tid = tr.get("id", "<без id>")
        where = f"{tid} «{tr.get('name', tid)}»"
        if tid in seen:
            rep.err(where, "дублирующийся id тренда")
        seen.add(tid)

        missing = [s for s in tr.get("signal_ids", []) if s not in known]
        if missing:
            rep.err(where, f"ссылки на несуществующие сигналы: {', '.join(missing)}")

        sigs = [index[s] for s in tr.get("signal_ids", []) if s in index]
        if len(sigs) < 3:
            rep.err(where, f"привязано {len(sigs)} сигналов; минимум для кандидата в тренды — три")

        independent = T.independent_count(tr, index)
        if independent < 3:
            rep.err(where, f"независимых сигналов {independent} после схлопывания общих первоисточников; "
                           f"перепечатки триангуляцией не считаются")

        families = {s.get("source_type") for s in sigs}
        if len(families) < 2:
            rep.err(where, "все сигналы одного типа источника; нужно минимум два семейства")
        if not (families & T.BEHAVIORAL_TYPES):
            rep.warn(where, "нет поведенческого или транзакционного сигнала — внимание подтверждено, поведение нет")
        if not (families & T.STRUCTURAL_TYPES):
            rep.warn(where, "нет структурного или институционального сигнала (регулирование, статистика, патент)")

        if len(set(tr.get("steep") or [])) < 2:
            rep.warn(where, "тренд опирается на один домен STEEP — вероятно, это отраслевая новость, а не тренд")

        tiers = Counter(T.tier(s) for s in sigs)
        computed = T.compute(tr, weight_table)
        score = computed["score"]
        has_document_base = tiers.get("T1", 0) >= 1 or tiers.get("T2", 0) >= 2
        if score >= T.MIN_SCORE_WITHOUT_DOCUMENT and not has_document_base:
            rep.err(where, f"score {score} при отсутствии первичных документов "
                           f"(T1={tiers.get('T1', 0)}, T2={tiers.get('T2', 0)}). "
                           f"Выше {T.MIN_SCORE_WITHOUT_DOCUMENT} нужен хотя бы один документ T1 или два источника T2")
        if tiers.get("T4", 0) > len(sigs) / 2:
            rep.warn(where, "больше половины сигналов — пересказы и агрегаторы")

        if not (tr.get("disconfirming") or "").strip():
            rep.err(where, "пустое поле disconfirming: отдельный поиск на опровержение обязателен")
        if not (tr.get("counter_trend") or "").strip():
            rep.warn(where, "не найден контртренд — почти всегда означает, что его не искали")
        if not (tr.get("adoption_cost") or "").strip():
            rep.warn(where, "не названа цена принятия (деньги, усилия, приватность, доверие, статус)")
        if not (tr.get("drivers") or []):
            rep.err(where, "не названы драйверы: без механизма это наблюдение, а не тренд")

        scores = tr.get("scores") or {}
        for key, value in scores.items():
            if key not in valid_keys:
                rep.err(where, f"неизвестный критерий скоринга: {key}")
            elif value is not None and not (0 <= float(value) <= 5):
                rep.err(where, f"оценка {key}={value} вне шкалы 0–5")
        if scores.get("novelty") == 5 and (scores.get("behavior_change") or 0) <= 1:
            rep.warn(where, "высокая новизна при отсутствии изменения поведения — типичный профиль хайпа")

        cov = computed["coverage"]
        if cov < 70:
            gaps = ", ".join(g[0] for g in computed["gaps"][:4])
            rep.warn(where, f"coverage {cov}% — результат публикуется только как предварительный; пробелы: {gaps}")
        if tr.get("confidence") == "high" and cov < 85:
            rep.err(where, f"проставлена высокая уверенность при coverage {cov}% (порог 85%)")

        decision = tr.get("decision")
        if decision not in T.DECISIONS:
            rep.err(where, "не задано решение (ignore / monitor / test / option / scale)")
        else:
            label = T.L(T.DECISIONS[decision])
            if decision in {"test", "option", "scale"}:
                if not tr.get("experiment"):
                    rep.warn(where, f"решение «{label}» без описанного эксперимента или шага")
                if not tr.get("stop_rule"):
                    rep.warn(where, f"решение «{label}» без stop-rule")
                if not tr.get("owner"):
                    rep.err(where, f"решение «{label}» без владельца")
        if decision == "scale" and (scores.get("behavior_change") or 0) < 3:
            rep.err(where, "решение «масштабировать» при неподтверждённом изменении поведения")

        if not (tr.get("indicators") or []):
            rep.err(where, "нет индикаторов наблюдения: тренд нельзя ни подтвердить, ни опровергнуть")
        else:
            kinds = {i.get("kind") for i in tr["indicators"]}
            if "leading" not in kinds:
                rep.warn(where, "все индикаторы отстающие — раннего предупреждения не будет")
            for i in tr["indicators"]:
                if not i.get("threshold"):
                    rep.warn(where, f"индикатор «{i.get('name')}» без порога срабатывания")

        if tr.get("probability") and not tr.get("probability_condition"):
            rep.warn(where, "вероятность задана без условия («при сохранении… и отсутствии…»)")
        if tr.get("impact") and not tr.get("impact_object"):
            rep.warn(where, "влияние задано без объекта (клиент, выручка, затраты, риск, бренд, компетенции)")

        statement = (tr.get("statement") or "").lower()
        # 1) глаголы и отглагольные формы движения/перехода
        change_verbs = (r"(меня|переход|сдвиг|переста|начина|раст[её]т|падает|снижа|смещ|"
                        r"становит|рушит|уход|превраща|слабе|отвяз|обгон|покупает|продаёт|ускоря|дроб|"
                        r"вход|замеща|вытесня|расшир|сужа|размыва|схлоп|забира|отдаю|терят|теряе|"
                        r"переориент|перестра|перерасп|переезжа|возвраща|появля|исчеза|копит|"
                        r"обнаружил|сокраща|увеличива|разрыв|shift|move|replac|grow|declin|shrink|"
                        r"erod|collaps|migrat)")
        # 2) явные маркеры направления: «вместо», «с X на Y», сравнительная степень
        direction = (r"(вместо\b|\bс\b.{1,60}\bна\b|→|быстрее|медленнее|больше|меньше|выше|ниже|"
                     r"чаще|реже|дороже|дешевле|instead of|from .{1,40} to )")
        if statement and not (re.search(change_verbs, statement) or re.search(direction, statement)):
            rep.warn(where, "формулировка не описывает изменение — проверьте, что это тренд, а не тема")
        name = (tr.get("name") or "").lower()
        if re.search(r"(изация|ность|ация)\b", name) and len(name.split()) < 3:
            rep.warn(where, "имя выглядит как абстрактное существительное — под него подходит что угодно")


def check_meta(data, rep, profile):
    meta = data.get("meta", {})
    if profile != "default":
        rep.note(f"Профиль весов: {profile} (по умолчанию — default). Смена профиля меняет score, "
                 f"поэтому сравнение с прошлыми волнами корректно только при том же профиле")
    if not (meta.get("decision_questions") or []):
        rep.warn("meta", "не заданы decision questions — не с чем сверять пользу отчёта")
    else:
        for q in meta["decision_questions"]:
            if not q.get("owner") or not q.get("decision_date"):
                rep.warn("meta", f"вопрос «{q.get('question', '')[:40]}…» без владельца или даты решения")
    if not meta.get("horizon"):
        rep.err("meta", "не задан горизонт")
    if not data.get("rejected"):
        rep.warn("meta", "нет отклонённых кандидатов: отбор без отбраковки обычно означает, "
                         "что кандидатов было слишком мало")

    depth = meta.get("depth")
    n = len(data.get("signals", []))
    minimum = {"express": 20, "full": 50, "deep": 100}.get(depth)
    if minimum and n < minimum:
        rep.warn("meta", f"глубина заявлена как «{depth}», но собрано {n} сигналов при нормативе {minimum}+")


def main():
    ap = argparse.ArgumentParser(description="Валидация trends.json перед генерацией артефактов")
    ap.add_argument("input")
    ap.add_argument("--strict", action="store_true", help="считать предупреждения ошибками")
    args = ap.parse_args()

    data = T.load(args.input)
    profile, weight_table = T.weights(data)
    rep = Report()
    check_meta(data, rep, profile)
    check_signals(data, rep)
    check_trends(data, rep, weight_table)

    for msg in rep.notes:
        print(f"  · {msg}")
    if rep.warnings:
        print(f"\nПредупреждения ({len(rep.warnings)}):")
        for where, msg in rep.warnings:
            print(f"  ! {where}: {msg}")
    if rep.errors:
        print(f"\nОшибки ({len(rep.errors)}):")
        for where, msg in rep.errors:
            print(f"  ✗ {where}: {msg}")
        print("\nГенерация артефактов заблокирована. Исправьте ошибки или понизьте статус трендов.")
        sys.exit(1)
    if args.strict and rep.warnings:
        print("\nРежим --strict: предупреждения считаются ошибками.")
        sys.exit(1)
    print(f"\nПроверка пройдена: {len(data['signals'])} сигналов, {len(data['trends'])} трендов"
          + (f", предупреждений — {len(rep.warnings)}" if rep.warnings else ", без замечаний"))


if __name__ == "__main__":
    main()
