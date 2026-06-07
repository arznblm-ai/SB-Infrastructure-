"use client";

import { useMemo, useState } from "react";
import {
  buildForecast,
  crmUpside,
  type AccountRow,
  type ConfirmedProjectRow,
  type CrmOpportunityRow,
  type ObligationRow
} from "@/lib/finance-math";
import { money } from "@/lib/money";

export function RubCockpit({
  accounts,
  confirmedProjects,
  crmOpportunities,
  obligations
}: {
  accounts: AccountRow[];
  confirmedProjects: ConfirmedProjectRow[];
  crmOpportunities: CrmOpportunityRow[];
  obligations: ObligationRow[];
}) {
  const [monthlyExpensesRub, setMonthlyExpensesRub] = useState(400000);
  const [useManualMonthlyExpenses, setUseManualMonthlyExpenses] = useState(true);
  const [includeCryptoReserve, setIncludeCryptoReserve] = useState(true);
  const [conservativeMode, setConservativeMode] = useState(false);

  const includeMap = useMemo(
    () => Object.fromEntries(confirmedProjects.map((p) => [p.id, p.includeInForecast === 1])) as Record<number, boolean>,
    [confirmedProjects]
  );

  const forecast = useMemo(
    () =>
      buildForecast(accounts, confirmedProjects, obligations, {
        monthlyExpensesRub,
        useManualMonthlyExpenses,
        includeCryptoReserve,
        conservativeMode,
        horizonMonths: 6,
        projectIncluded: includeMap
      }),
    [accounts, confirmedProjects, obligations, monthlyExpensesRub, useManualMonthlyExpenses, includeCryptoReserve, conservativeMode, includeMap]
  );

  const crm = useMemo(() => crmUpside(crmOpportunities), [crmOpportunities]);

  return (
    <div className="mx-auto flex max-w-7xl flex-col gap-6 px-5 py-5">
      <section className="grid gap-3 md:grid-cols-2 xl:grid-cols-5">
        <Card title="Деньги сейчас" value={money(forecast.startBalance)} />
        <Card title="Подтверждено к приходу" value={money(forecast.confirmedExpected)} />
        <Card title="Обязательства" value={money(forecast.businessDebt + forecast.personalDebt)} />
        <Card title="Минимальный баланс" value={money(forecast.minBalance)} detail={formatMonthRu(forecast.minBalanceMonth)} />
        <Card title="Кассовый разрыв" value={forecast.hasCashGap ? "Есть разрыв" : "Ок"} detail={money(forecast.endBalance)} />
      </section>

      <section className="rounded-lg border border-[var(--line)] bg-[var(--panel)] p-4">
        <h2 className="text-base font-semibold">Параметры прогноза</h2>
        <div className="mt-3 grid gap-3 md:grid-cols-2 xl:grid-cols-4">
          <Control label={`Расходы в месяц (manual): ${money(monthlyExpensesRub)}`}>
            <input
              type="range"
              min={200000}
              max={700000}
              step={10000}
              value={monthlyExpensesRub}
              onChange={(e) => setMonthlyExpensesRub(Number(e.target.value))}
            />
          </Control>
          <Control label="Источник monthly расходов">
            <label className="flex items-center gap-2 text-xs">
              <input type="checkbox" checked={useManualMonthlyExpenses} onChange={(e) => setUseManualMonthlyExpenses(e.target.checked)} />
              {useManualMonthlyExpenses ? "ручной режим" : "из обязательств"}
            </label>
          </Control>
          <Control label="Учитывать крипто-резерв">
            <input type="checkbox" checked={includeCryptoReserve} onChange={(e) => setIncludeCryptoReserve(e.target.checked)} />
          </Control>
          <Control label="Conservative mode (сдвиг +1 месяц)">
            <input type="checkbox" checked={conservativeMode} onChange={(e) => setConservativeMode(e.target.checked)} />
          </Control>
        </div>
        <p className="mt-2 text-xs text-[var(--muted)]">
          Сейчас используется:{" "}
          <b>
              {forecast.monthlyExpenseSource === "manual"
                ? `ручной режим (${money(forecast.monthlyExpenseUsed)})`
              : `из обязательств (${money(forecast.monthlyExpenseFromObligations)})`}
          </b>
        </p>
      </section>

      <section className="rounded-lg border border-[var(--line)] bg-[var(--panel)]">
        <div className="border-b border-[var(--line)] px-4 py-3">
          <h2 className="text-base font-semibold">Прогноз баланса по месяцам</h2>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full min-w-[760px] border-collapse text-sm">
            <thead className="bg-[#eef1e7] text-left text-xs uppercase text-[var(--muted)]">
              <tr>
                <th className="px-4 py-3">Месяц</th>
                <th className="px-4 py-3">Приход</th>
                <th className="px-4 py-3">Выплаты</th>
                <th className="px-4 py-3">Налоги</th>
                <th className="px-4 py-3">Баланс на конец месяца</th>
                <th className="px-4 py-3">Статус</th>
              </tr>
            </thead>
            <tbody>
              {forecast.months.map((m) => (
                <tr key={m.month} className="border-t border-[var(--line)]">
                  <td className="px-4 py-3">{formatMonthRu(m.month)}</td>
                  <td className="px-4 py-3">{money(m.income)}</td>
                  <td className="px-4 py-3">{money(m.payouts)}</td>
                  <td className="px-4 py-3">{money(m.taxes)}</td>
                  <td className="px-4 py-3">{money(m.endBalance)}</td>
                  <td className="px-4 py-3">{statusRu(m.status)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div className="grid gap-2 px-4 py-3 text-sm md:grid-cols-4">
          <div>Минимальный баланс: <b>{money(forecast.minBalance)}</b></div>
          <div>Месяц минимума: <b>{formatMonthRu(forecast.minBalanceMonth)}</b></div>
          <div>Баланс на конец горизонта: <b>{money(forecast.endBalance)}</b></div>
          <div>Кассовый разрыв: <b>{forecast.hasCashGap ? "есть" : "нет"}</b></div>
        </div>
      </section>

      <section className="rounded-lg border border-[var(--line)] bg-[var(--panel)]">
        <div className="border-b border-[var(--line)] px-4 py-3">
          <h2 className="text-base font-semibold">Активы и обязательства</h2>
        </div>
        <div className="grid gap-2 px-4 py-3 text-sm md:grid-cols-5">
          <div>Ликвидные счета: <b>{money(forecast.liquidNow)}</b></div>
          <div>Крипто-резерв в рублевой оценке: <b>{money(forecast.cryptoReserve)}</b></div>
          <div>Долги бизнеса: <b>{money(forecast.businessDebt)}</b></div>
          <div>Личные долги: <b>{money(forecast.personalDebt)}</b></div>
          <div>Чистая позиция: <b>{money(forecast.netPosition)}</b></div>
        </div>
      </section>

      <section className="rounded-lg border border-[var(--line)] bg-[var(--panel)]">
        <div className="border-b border-[var(--line)] px-4 py-3">
          <h2 className="text-base font-semibold">Подтвержденные проекты</h2>
        </div>
        <TableConfirmed projects={confirmedProjects} />
      </section>

      <section className="rounded-lg border border-[var(--line)] bg-[var(--panel)]">
        <div className="border-b border-[var(--line)] px-4 py-3">
          <h2 className="text-base font-semibold">CRM / запросы в продаже</h2>
        </div>
        <TableCrm rows={crmOpportunities} />
        <div className="grid gap-2 border-t border-[var(--line)] px-4 py-3 text-sm md:grid-cols-4">
          <div>Потенциал выручки: <b>{money(crm.potentialRevenue)}</b></div>
          <div>Потенциал прибыли 40-60%: <b>{money(crm.minProfit)} - {money(crm.maxProfit)}</b></div>
          <div>Среднее ожидание: <b>{money(crm.avgProfit)}</b></div>
          <div><b>CRM-апсайд не включен в базовый прогноз</b></div>
        </div>
      </section>

      <section className="rounded-lg border border-[var(--line)] bg-[var(--panel)]">
        <div className="border-b border-[var(--line)] px-4 py-3">
          <h2 className="text-base font-semibold">Налоги</h2>
        </div>
        <div className="grid gap-2 px-4 py-3 text-sm md:grid-cols-4">
          <div>Уже оплачено: <b>{money(forecast.fixedTaxPaid)}</b></div>
          <div>Предстоящие фиксированные налоги: <b>{money(forecast.fixedTaxUpcoming)}</b></div>
          <div>Налог с новых приходов: <b>{money(forecast.autoTaxFromIncome)}</b></div>
          <div>Источник налога: <b>ставка проекта</b></div>
        </div>
      </section>

      <section className="rounded-lg border border-[var(--line)] bg-[var(--panel)] p-4 text-sm">
        <p>Все расчеты выполняются в RUB. Ввод новых проектов и обязательств идет через чат; веб-интерфейс остается read-only дешбордом для просмотра общей картины.</p>
      </section>
    </div>
  );
}

function Card({ title, value, detail }: { title: string; value: string; detail?: string }) {
  return (
    <div className="rounded-lg border border-[var(--line)] bg-[var(--panel)] p-4">
      <p className="text-xs text-[var(--muted)]">{title}</p>
      <p className="mt-2 text-2xl font-semibold">{value}</p>
      {detail ? <p className="mt-1 text-xs text-[var(--muted)]">{detail}</p> : null}
    </div>
  );
}

function Control({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="grid gap-1 rounded-md border border-[var(--line)] p-2 text-xs">
      <span className="text-[var(--muted)]">{label}</span>
      {children}
    </label>
  );
}

function statusRu(status: "stabilno" | "napryazhenno" | "tonko" | "minus") {
  if (status === "stabilno") return "стабильно";
  if (status === "napryazhenno") return "напряженно";
  if (status === "tonko") return "тонко";
  return "минус";
}

function formatMonthRu(month: string) {
  const [year, mon] = month.split("-").map(Number);
  if (!year || !mon) return month;

  const formatted = new Intl.DateTimeFormat("ru-RU", {
    month: "long",
    year: "numeric"
  }).format(new Date(year, mon - 1, 1));

  return formatted.replace(" г.", "");
}

function TableConfirmed({ projects }: { projects: ConfirmedProjectRow[] }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[980px] border-collapse text-sm">
        <thead className="bg-[#eef1e7] text-left text-xs uppercase text-[var(--muted)]">
          <tr>
            <th className="px-4 py-3">Проект</th>
            <th className="px-4 py-3">Выручка</th>
            <th className="px-4 py-3">Налог</th>
            <th className="px-4 py-3">Срок / месяц прихода</th>
            <th className="px-4 py-3">Статус</th>
            <th className="px-4 py-3">Клиент</th>
          </tr>
        </thead>
        <tbody>
          {projects.map((p) => (
            <tr key={p.id} className="border-t border-[var(--line)]">
              <td className="px-4 py-3">{p.name}</td>
              <td className="px-4 py-3">{money(p.revenueRub)}</td>
              <td className="px-4 py-3">{money((p.revenueRub * p.taxRate) / 100)}</td>
              <td className="px-4 py-3">{formatMonthRu(p.expectedMonth)}</td>
              <td className="px-4 py-3">{p.status}</td>
              <td className="px-4 py-3">{p.clientName ?? "-"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function TableCrm({ rows }: { rows: CrmOpportunityRow[] }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[1100px] border-collapse text-sm">
        <thead className="bg-[#eef1e7] text-left text-xs uppercase text-[var(--muted)]">
          <tr>
            <th className="px-4 py-3">Название</th>
            <th className="px-4 py-3">Смета / диапазон</th>
            <th className="px-4 py-3">Маржа</th>
            <th className="px-4 py-3">Потенциальная прибыль</th>
            <th className="px-4 py-3">Статус</th>
            <th className="px-4 py-3">Следующий шаг</th>
            <th className="px-4 py-3">Дата следующего контакта</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((o) => {
            const pMin = (o.estimateMinRub * o.marginMinPercent) / 100;
            const pMax = (o.estimateMaxRub * o.marginMaxPercent) / 100;
            return (
              <tr key={o.id} className="border-t border-[var(--line)]">
                <td className="px-4 py-3">{o.name}</td>
                <td className="px-4 py-3">{money(o.estimateMinRub)} - {money(o.estimateMaxRub)}</td>
                <td className="px-4 py-3">{o.marginMinPercent}-{o.marginMaxPercent}%</td>
                <td className="px-4 py-3">{money((pMin + pMax) / 2)}</td>
                <td className="px-4 py-3">{o.status}</td>
                <td className="px-4 py-3">{o.nextStep ?? "-"}</td>
                <td className="px-4 py-3">{o.nextContactDate ?? "-"}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
