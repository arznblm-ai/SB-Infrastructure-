"""Общая логика для скриптов трендвотчинга.

Держит в одном месте веса скоринговой модели, уровни доказательности,
расчёт score/coverage/confidence и подписи интерфейса на двух языках,
чтобы валидатор, отчёт и радар никогда не разошлись между собой.
"""

import json
import sys
from urllib.parse import urlparse

# --- Скоринговая модель -----------------------------------------------------
# 15 критериев. Порядок = порядок в scoring sheet отчёта.
CRITERIA = [
    ("novelty",                  "Новизна / отличимость",                   "Novelty / distinctiveness"),
    ("independent_signals",      "Независимые сигналы",                     "Independent signals"),
    ("source_quality",           "Качество источников",                     "Source quality"),
    ("momentum",                 "Временной импульс",                       "Momentum"),
    ("behavior_change",          "Наблюдаемое изменение поведения",         "Observed behaviour change"),
    ("persistence",              "Устойчивость во времени",                 "Persistence over time"),
    ("geo_breadth",              "Географическая широта",                   "Geographic breadth"),
    ("cross_industry",           "Межотраслевая широта",                    "Cross-industry breadth"),
    ("investment",               "Инвестиции и необратимые обязательства",  "Investment and irreversible commitment"),
    ("driver_durability",        "Долговечность драйверов",                 "Driver durability"),
    ("institutionalization",     "Институционализация",                     "Institutionalisation"),
    ("barrier_surmountability",  "Преодолимость барьеров",                  "Barrier surmountability"),
    ("counter_trend_resilience", "Устойчивость к контртренду",              "Counter-trend resilience"),
    ("continuation_probability", "Вероятность продолжения",                 "Probability of continuation"),
    ("potential_impact",         "Потенциальное влияние",                   "Potential impact"),
]

# Профили весов. default подходит большинству рынков; остальные — поправки под
# тип отрасли. Сумма любого профиля равна 100; смена профиля фиксируется в
# meta.weight_profile и печатается в отчёте, чтобы результат оставался сопоставим.
WEIGHT_PROFILES = {
    # Универсальный: наказывает недоказанное, вознаграждает подтверждённое поведением.
    "default": {
        "novelty": 4, "independent_signals": 11, "source_quality": 7, "momentum": 9,
        "behavior_change": 10, "persistence": 6, "geo_breadth": 5, "cross_industry": 5,
        "investment": 6, "driver_durability": 9, "institutionalization": 5,
        "barrier_surmountability": 4, "counter_trend_resilience": 4,
        "continuation_probability": 7, "potential_impact": 8,
    },
    # Зарегулированные рынки (фарма, финансы, энергетика, инфраструктура):
    # без регистрации, лицензии или стандарта рынка нет вообще.
    "regulated": {
        "novelty": 3, "independent_signals": 10, "source_quality": 8, "momentum": 6,
        "behavior_change": 8, "persistence": 6, "geo_breadth": 4, "cross_industry": 4,
        "investment": 7, "driver_durability": 9, "institutionalization": 12,
        "barrier_surmountability": 6, "counter_trend_resilience": 3,
        "continuation_probability": 6, "potential_impact": 8,
    },
    # Быстрые потребительские и креативные рынки (мода, медиа, развлечения):
    # институты почти не участвуют, зато решает скорость и широта распространения.
    "consumer": {
        "novelty": 6, "independent_signals": 11, "source_quality": 6, "momentum": 12,
        "behavior_change": 12, "persistence": 8, "geo_breadth": 6, "cross_industry": 6,
        "investment": 4, "driver_durability": 8, "institutionalization": 2,
        "barrier_surmountability": 3, "counter_trend_resilience": 5,
        "continuation_probability": 5, "potential_impact": 6,
    },
    # Глубокие технологии и B2B с длинным циклом: поведения ещё нет по определению,
    # доказательства идут из науки, патентов и необратимых обязательств.
    "deeptech": {
        "novelty": 6, "independent_signals": 10, "source_quality": 9, "momentum": 7,
        "behavior_change": 5, "persistence": 5, "geo_breadth": 4, "cross_industry": 6,
        "investment": 10, "driver_durability": 10, "institutionalization": 6,
        "barrier_surmountability": 6, "counter_trend_resilience": 3,
        "continuation_probability": 6, "potential_impact": 7,
    },
}

STATUS = [
    (0,  39,  ("Сигнал / недостаточно доказательств", "Signal / insufficient evidence"),
              ("Сохранить, проверить источник или закрыть", "Keep, verify the source or close")),
    (40, 54,  ("Emerging hypothesis", "Emerging hypothesis"),
              ("Добавить контрдоказательства и индикаторы", "Add disconfirming evidence and indicators")),
    (55, 69,  ("Зарождающийся тренд", "Emerging trend"),
              ("Мониторить и тестировать точечно", "Monitor and test selectively")),
    (70, 84,  ("Подтверждённый тренд", "Validated trend"),
              ("Создавать опционы и селективные ставки", "Create options and selective bets")),
    (85, 100, ("Структурный / установившийся", "Structural / established"),
              ("Встраивать в базовые сценарии и операционную модель", "Embed in base scenarios and operating model")),
]

DECISIONS = {
    "ignore":  ("Игнорировать", "Ignore"),
    "monitor": ("Мониторить", "Monitor"),
    "test":    ("Проверить", "Test"),
    "option":  ("Создать опцион", "Create an option"),
    "scale":   ("Масштабировать", "Scale"),
}
DECISION_RINGS = ["scale", "option", "test", "monitor"]

STEEP = {
    "S": ("Общество и культура", "Society and culture"),
    "T": ("Технологии", "Technology"),
    "E": ("Экономика", "Economy"),
    "N": ("Среда и ресурсы", "Environment and resources"),
    "P": ("Регулирование", "Regulation"),
    "B": ("Отрасль и игроки", "Industry and players"),
}

STAGES = [
    ("Латентные напряжения", "Latent tensions"),
    ("Слабые сигналы", "Weak signals"),
    ("Emerging issue", "Emerging issue"),
    ("Раннее принятие", "Early adoption"),
    ("Ускорение", "Acceleration"),
    ("Mainstream", "Mainstream"),
    ("Фрагментация / контртренд", "Fragmentation / counter-trend"),
    ("Норма / спад / трансформация", "Norm / decline / transformation"),
]

CONFIDENCE = {
    "high": ("высокая", "high"),
    "medium": ("средняя", "medium"),
    "low": ("низкая", "low"),
    None: ("не задана", "not set"),
}

CURVES = [
    ("attention", "Внимание", "Attention"),
    ("capability", "Возможность", "Capability"),
    ("behavior", "Поведение", "Behaviour"),
    ("economics", "Экономика", "Economics"),
    ("institutions", "Институты", "Institutions"),
]

DEPTHS = {
    "express": ("экспресс", "express"),
    "full": ("полный", "full"),
    "deep": ("глубокий", "deep"),
}

# --- Уровни доказательности -------------------------------------------------
# T1 — первичный документ: НПА, отчётность, регуляторный отчёт, исследование
#      с раскрытой методикой, стандарт, статистический сборник.
# T2 — первичная публикация организации без документа, либо факт официального
#      органа, переданный через деловое медиа (см. 06-sources, раздел 10).
# T3 — профессиональное медиа или аналитик с собственным наблюдением.
# T4 — пересказ и агрегатор без собственных данных.

DOC_KINDS = {"pdf", "doc", "docx", "xls", "xlsx", "dataset"}
T1_SOURCE_TYPES = {"regulation", "official_stats", "research", "patent"}
T2_SOURCE_TYPES = {"product_launch", "investment", "job_posting", "internal",
                   "startup", "marketplace", "association"}
BEHAVIORAL_TYPES = {"official_stats", "marketplace", "search_query", "internal", "product_launch", "job_posting"}
STRUCTURAL_TYPES = {"regulation", "official_stats", "patent", "research", "association"}

MIN_SCORE_WITHOUT_DOCUMENT = 55


def L(pair, lang="ru"):
    """Достаёт подпись из пары (ru, en)."""
    if isinstance(pair, (tuple, list)):
        return pair[1] if lang == "en" and len(pair) > 1 else pair[0]
    return pair


def weights(data=None):
    """Веса активного профиля. Профиль задаётся в meta.weight_profile."""
    profile = "default"
    if data:
        profile = (data.get("meta") or {}).get("weight_profile") or "default"
    if profile not in WEIGHT_PROFILES:
        sys.exit(f"Неизвестный профиль весов «{profile}». Доступны: {', '.join(WEIGHT_PROFILES)}")
    table = WEIGHT_PROFILES[profile]
    return profile, [(key, ru, en, table[key]) for key, ru, en in CRITERIA]


def tier(signal):
    """Уровень доказательности сигнала. Явный tier в данных всегда сильнее вывода."""
    if signal.get("tier") in {"T1", "T2", "T3", "T4"}:
        return signal["tier"]
    doc = signal.get("doc") or {}
    kind = (doc.get("kind") or "").lower()
    stype = signal.get("source_type")
    primacy = signal.get("primacy")
    reliability = signal.get("reliability")

    if primacy == "secondary":
        # Факт официального органа, переданный через медиа, остаётся фактом органа:
        # первоисточником является ведомство, даже если документа в открытом доступе нет.
        if stype in {"regulation", "official_stats"} and reliability == "A":
            return "T2"
        return "T4"
    if kind in DOC_KINDS and (reliability in {"A", "B"} or stype in T1_SOURCE_TYPES):
        return "T1"
    if stype in T1_SOURCE_TYPES and reliability == "A":
        return "T1"
    if stype in T2_SOURCE_TYPES or (stype in T1_SOURCE_TYPES and reliability == "B"):
        return "T2"
    if reliability == "C":
        return "T4"
    return "T3"


def domain(url):
    if not url:
        return None
    try:
        host = urlparse(url).netloc.lower()
    except ValueError:
        return None
    return host[4:] if host.startswith("www.") else host


# --- Расчёты ----------------------------------------------------------------

def compute(trend, weight_table=None):
    """Считает score и coverage.

    N/D (None) никогда не заменяется нулём: критерий выпадает из покрытия.
    Поэтому нехватка данных снижает coverage, а не балл.
    """
    if weight_table is None:
        weight_table = [(k, ru, en, WEIGHT_PROFILES["default"][k]) for k, ru, en in CRITERIA]
    scores = trend.get("scores") or {}
    total = 0.0
    covered = 0
    gaps = []
    for key, ru, en, weight in weight_table:
        value = scores.get(key)
        if value is None:
            gaps.append((ru, en))
            continue
        total += weight * float(value) / 5.0
        covered += weight
    normalized = (total / covered * 100.0) if covered else None
    return {
        "score": round(total, 1),
        "normalized": round(normalized, 1) if normalized is not None else None,
        "coverage": covered,  # веса в сумме 100, поэтому covered уже проценты
        "gaps": gaps,
    }


def status_for(score, lang="ru"):
    for lo, hi, name, action in STATUS:
        if lo <= score <= hi:
            return L(name, lang), L(action, lang)
    return "—", "—"


def confidence_hint(trend, computed, index=None):
    """Подсказка по уверенности, если она не проставлена вручную.

    Порог высокой уверенности повторяет правило из references/03-scoring.md:
    coverage ≥ 85%, минимум четыре класса источников, поведенческое И структурное
    подтверждение, найденные контрдоказательства. Ручное значение важнее.
    """
    if trend.get("confidence"):
        return trend["confidence"]
    cov = computed["coverage"]
    has_counter = bool((trend.get("disconfirming") or "").strip())
    if index is not None:
        sigs = [index[s] for s in trend.get("signal_ids", []) if s in index]
        families = {s.get("source_type") for s in sigs}
        rich = (len(families) >= 4
                and bool(families & BEHAVIORAL_TYPES)
                and bool(families & STRUCTURAL_TYPES))
        if cov >= 85 and has_counter and rich:
            return "high"
    if cov >= 70 and has_counter:
        return "medium"
    return "low"


def load(path):
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    for block in ("meta", "signals", "trends"):
        if block not in data:
            sys.exit(f"В {path} нет обязательного блока «{block}» — см. assets/trends.schema.json")
    return data


def lang_of(data, override=None):
    """Язык вывода: флаг командной строки сильнее meta.lang, по умолчанию русский."""
    if override:
        return override
    return (data.get("meta") or {}).get("lang") or "ru"


def signals_index(data):
    return {s["id"]: s for s in data.get("signals", [])}


def independent_count(trend, index):
    """Независимые сигналы: с общим первоисточником считаются одним."""
    groups = []
    for sid in trend.get("signal_ids", []):
        sig = index.get(sid)
        if not sig:
            continue
        linked = set(sig.get("independent_of") or []) | {sid}
        merged = None
        for group in groups:
            if group & linked:
                group |= linked
                merged = group
                break
        if merged is None:
            groups.append(set(linked))
    return len(groups)
