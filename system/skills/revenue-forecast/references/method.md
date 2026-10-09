# Методика прогноза выручки: выжимка исследования

### [[2026-10-09]]

> Источник: Research Dept, 09.10.2026 — `infrastructure/Research Dept/workspace/startup-revenue-forecast-2026-10-09/` (отчёт, `benchmarks-table.md` на 41 строку с URL, `sources.md`). Здесь — то, что нужно для расчёта. Бенчмарки стареют: перед питчем перепроверять первоисточник.

## Методология

1. **Bottom-up обязателен.** Рынок = число клиентов × годовой чек; SOM — накопленные клиенты из модели продаж, не «% от TAM» ([Pear VC](https://pear.vc/market-sizing-guide/)).
2. **Выручка = минимум из трёх ограничений:** спрос, ёмкость продаж, ёмкость внедрения ([Equidam](https://www.equidam.com/financial-projections-bottom-up-approach)).
3. **Продажи — от фактической продуктивности, а не квоты**, с разгоном и текучестью продавцов ([Kellblog](https://www.kellblog.com/how-to-make-and-use-a-proper-sales-bookings-productivity-and-quota-capacity-model/)). Квоту в 2026 выполнили 48% продавцов ([Bridge Group](https://blog.bridgegroupinc.com/2026-ae-compensation-quota-ai-metrics)).
4. **Когорты поверх продаж:** удержание и расширение. Годовой отток = 1 − (1 − месячный)^12 ([Point Nine](https://writing.pointnine.com/p/saas-metrics-benchmarking-your-churn)).
5. **Юнит-экономика — проверка, не вход:** LTV/CAC > 3, окупаемость CAC < 12 мес ([Skok](https://www.forentrepreneurs.com/saas-metrics-2/)); LTV по марже ([a16z](https://a16z.com/16-startup-metrics/)).
6. **Сценарии через драйверы;** tornado по циклу сделки, конверсии пилота, ёмкости внедрения, цене, оттоку ([CFI](https://corporatefinanceinstitute.com/resources/financial-modeling/what-is-sensitivity-analysis/)).
7. **T2D3 — путь победителей.** Медиана роста частных SaaS 22% ([SaaS Capital](https://www.saas-capital.com/research/private-saas-company-growth-rate-benchmarks/)); до $1M ARR за 3 года — 13,4% ([ChartMogul](https://chartmogul.com/reports/saas-growth-the-odds-of-making-it/)).
8. **On-prem / лицензии:** раздельно bookings, выручка, ARR; разовая лицензия — не ARR; внедрение и поддержка — отдельные потоки.

## Бенчмарки для драйверов

| Драйвер | Диапазон | Качество | Источник |
|---|---|---|---|
| Платный пилот → годовой контракт | 60–90% | мнение практика | [SaaStr](https://www.saastr.com/what-is-the-typical-conversion-from-paid-pilot-to-annual-contract-in-b2b-saas-2) |
| ИИ-пилот → продакшн | ~54% | пересказ Gartner 2022 | [Gartner](https://www.gartner.com/en/newsroom/press-releases/2022-08-22-gartner-survey-reveals-80-percent-of-executives-think-automation-can-be-applied-to-any-business-decision) |
| GenAI-проекты, брошенные после пилота | ≥30% | прогноз Gartner 2024 | [Gartner](https://www.gartner.com/en/newsroom/press-releases/2024-07-29-gartner-predicts-30-percent-of-generative-ai-projects-will-be-abandoned-after-proof-of-concept-by-end-of-2025) |
| Цикл сделки | SMB 14–30 дн., mid-market 30–90 | вендор | [Optifai](https://optif.ai/learn/questions/sales-cycle-length-benchmark/) |
| Месячный отток | SMB 3–7%; mid/enterprise ~0,5% | данные 2015 | [Point Nine](https://writing.pointnine.com/p/saas-metrics-benchmarking-your-churn) |
| NRR | медиана 102% (97–111%) | исследование | [SaaS Capital](https://www.saas-capital.com/research/saas-retention-benchmarks-for-private-b2b-companies/) |
| Разгон продавца | ~6 мес | исследование 2026 | [Bridge Group](https://blog.bridgegroupinc.com/2026-ae-compensation-quota-ai-metrics) |
| Клиентов в месяц на основателя-продавца | ~1–5 | оценка по T2D3 | [TechCrunch](https://techcrunch.com/2015/02/01/the-saas-travel-adventure/) |

## Рынок РФ (для оценки SAM/SOM)

- МСП на 10.09.2026: малых 241 279, средних 24 398 ([реестр МСП](https://rmsp.nalog.ru/statistics.html)).
- Рекламные агентства (ОКВЭД 73.11): ~96% — микро; в Москве ~605 малых и ~40 средних, в Петербурге ~110 малых и средних. Полная разбивка по РФ — выгрузкой открытых данных реестра МСП ([ФНС](https://www.nalog.gov.ru/opendata/7707329152-rsmp/)).
- ИИ на уровне организации — 5,2% крупных и средних ([ИСИЭЗ ВШЭ](https://issek.hse.ru/news/1203025244.html)); нейросетями пользуются 53% МСП ([Деловая Россия](https://deloros.ru/press-centr/publikacii/podnyat-na-bot-polovina-msp-ispolzuet-neyroseti-dlya-sokrashcheniya-izderzhek-/)); главный барьер — затраты (54%).
- Публичных прайсов on-prem ИИ в РФ нет; рынок on-prem ИИ-платформ ~16 млрд ₽ ([kod.ru](https://kod.ru/yandex-selectel-metamentor-on-premises-ai-service)).
