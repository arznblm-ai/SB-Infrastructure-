export type AccountRow = {
  id: number;
  name: string;
  type: "cash" | "bank" | "card" | "crypto" | "reserve";
  balanceRub: number;
  referenceNote: string | null;
  updatedAt: string;
};

export type ConfirmedProjectRow = {
  id: number;
  clientId: number | null;
  clientName: string | null;
  name: string;
  revenueRub: number;
  taxRate: number;
  expectedMonth: string;
  expectedDate: string;
  includeInForecast: number;
  status: "received" | "expected" | "in_work" | "delayed" | "risk" | "done";
  notes: string | null;
};

export type CrmOpportunityRow = {
  id: number;
  clientId: number | null;
  clientName: string | null;
  name: string;
  estimateMinRub: number;
  estimateMaxRub: number;
  marginMinPercent: number;
  marginMaxPercent: number;
  status: "query" | "tender" | "proposal" | "waiting" | "risk" | "lost";
  nextStep: string | null;
  nextContactDate: string | null;
  notes: string | null;
};

export type ObligationRow = {
  id: number;
  label: string;
  type: "business_debt" | "personal_debt" | "tax" | "monthly_expense" | "one_time_expense";
  amountRub: number;
  dueMonth: string;
  dueDate: string;
  status: "planned" | "paid" | "paused";
  notes: string | null;
};

export type ForecastSettings = {
  monthlyExpensesRub: number;
  useManualMonthlyExpenses: boolean;
  includeCryptoReserve: boolean;
  conservativeMode: boolean;
  horizonMonths: number;
  projectIncluded: Record<number, boolean>;
};

export type ForecastMonth = {
  month: string;
  income: number;
  payouts: number;
  taxes: number;
  endBalance: number;
  status: "stabilno" | "napryazhenno" | "tonko" | "minus";
};

function monthKey(date: Date) {
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}`;
}

export function shiftMonth(month: string, by: number) {
  const [year, mon] = month.split("-").map(Number);
  const date = new Date(year, mon - 1 + by, 1);
  return monthKey(date);
}

export function nextMonths(from: string, count: number) {
  return Array.from({ length: count }, (_, i) => shiftMonth(from, i));
}

function statusByBalance(balance: number): ForecastMonth["status"] {
  if (balance >= 800000) return "stabilno";
  if (balance >= 250000) return "napryazhenno";
  if (balance >= 0) return "tonko";
  return "minus";
}

export function buildForecast(
  accounts: AccountRow[],
  projects: ConfirmedProjectRow[],
  obligations: ObligationRow[],
  settings: ForecastSettings
) {
  const today = new Date();
  const startMonth = monthKey(new Date(today.getFullYear(), today.getMonth(), 1));
  const months = nextMonths(startMonth, settings.horizonMonths);
  const monthSet = new Set(months);

  const liquidNow = accounts
    .filter((a) => a.type !== "crypto" && a.type !== "reserve")
    .reduce((sum, a) => sum + a.balanceRub, 0);
  const cryptoReserve = accounts
    .filter((a) => a.type === "crypto" || a.type === "reserve")
    .reduce((sum, a) => sum + a.balanceRub, 0);

  const startBalance = liquidNow + (settings.includeCryptoReserve ? cryptoReserve : 0);
  const monthlyExpenseFromObligations = obligations
    .filter((o) => o.type === "monthly_expense")
    .filter((o) => o.status !== "paid" && o.status !== "paused")
    .reduce((sum, o) => sum + o.amountRub, 0);
  const monthlyExpenseBaseline = settings.useManualMonthlyExpenses
    ? settings.monthlyExpensesRub
    : monthlyExpenseFromObligations;

  const monthlyRows: ForecastMonth[] = [];
  let running = startBalance;

  for (const month of months) {
    const incomeProjects = projects
      .filter((p) => settings.projectIncluded[p.id] !== false)
      .filter((p) => p.includeInForecast === 1)
      .filter((p) => ["expected", "in_work", "delayed", "risk"].includes(p.status))
      .filter((p) => {
        const targetMonth = settings.conservativeMode ? shiftMonth(p.expectedMonth, 1) : p.expectedMonth;
        return targetMonth === month;
      });
    const incomeFromProjects = incomeProjects.reduce((sum, p) => sum + p.revenueRub, 0);

    const fixedTax = obligations
      .filter((o) => o.status !== "paid" && o.status !== "paused")
      .filter((o) => o.type === "tax" && o.dueMonth === month)
      .reduce((sum, o) => sum + o.amountRub, 0);

    const oneTimeAndDebts = obligations
      .filter((o) => o.status !== "paid" && o.status !== "paused")
      .filter((o) => o.type !== "tax" && o.type !== "monthly_expense")
      .filter((o) => o.dueMonth === month)
      .reduce((sum, o) => sum + o.amountRub, 0);

    const monthlyExpense = monthlyExpenseBaseline;
    const dynamicTax = Math.round(
      incomeProjects.reduce((sum, p) => sum + (p.revenueRub * p.taxRate) / 100, 0)
    );
    const taxes = fixedTax + dynamicTax;
    const payouts = monthlyExpense + oneTimeAndDebts;

    running = running + incomeFromProjects - payouts - taxes;
    monthlyRows.push({
      month,
      income: incomeFromProjects,
      payouts,
      taxes,
      endBalance: running,
      status: statusByBalance(running)
    });
  }

  const minRow = monthlyRows.reduce((min, row) => (row.endBalance < min.endBalance ? row : min), monthlyRows[0]);

  const confirmedExpected = projects
    .filter((p) => settings.projectIncluded[p.id] !== false)
    .filter((p) => p.includeInForecast === 1)
    .filter((p) => ["expected", "in_work", "delayed", "risk"].includes(p.status))
    .filter((p) => {
      const targetMonth = settings.conservativeMode ? shiftMonth(p.expectedMonth, 1) : p.expectedMonth;
      return monthSet.has(targetMonth);
    })
    .reduce((sum, p) => sum + p.revenueRub, 0);

  const businessDebt = obligations
    .filter((o) => o.status !== "paid")
    .filter((o) => o.type === "business_debt")
    .reduce((sum, o) => sum + o.amountRub, 0);

  const personalDebt = obligations
    .filter((o) => o.status !== "paid")
    .filter((o) => o.type === "personal_debt")
    .reduce((sum, o) => sum + o.amountRub, 0);

  const fixedTaxPaid = obligations
    .filter((o) => o.type === "tax" && o.status === "paid")
    .reduce((sum, o) => sum + o.amountRub, 0);

  const fixedTaxUpcoming = obligations
    .filter((o) => o.type === "tax" && o.status !== "paid")
    .reduce((sum, o) => sum + o.amountRub, 0);

  const autoTaxFromIncome = Math.round(
    projects
      .filter((p) => settings.projectIncluded[p.id] !== false)
      .filter((p) => p.includeInForecast === 1)
      .filter((p) => ["expected", "in_work", "delayed", "risk"].includes(p.status))
      .filter((p) => {
        const targetMonth = settings.conservativeMode ? shiftMonth(p.expectedMonth, 1) : p.expectedMonth;
        return monthSet.has(targetMonth);
      })
      .reduce((sum, p) => sum + (p.revenueRub * p.taxRate) / 100, 0)
  );

  return {
    startBalance,
    liquidNow,
    cryptoReserve,
    businessDebt,
    personalDebt,
    netPosition: liquidNow + cryptoReserve - businessDebt - personalDebt,
    confirmedExpected,
    fixedTaxPaid,
    fixedTaxUpcoming,
    autoTaxFromIncome,
    monthlyExpenseFromObligations,
    monthlyExpenseUsed: monthlyExpenseBaseline,
    monthlyExpenseSource: settings.useManualMonthlyExpenses ? "manual" : "obligations",
    months: monthlyRows,
    minBalance: minRow.endBalance,
    minBalanceMonth: minRow.month,
    endBalance: monthlyRows[monthlyRows.length - 1]?.endBalance ?? startBalance,
    hasCashGap: monthlyRows.some((m) => m.endBalance < 0)
  };
}

export function crmUpside(opportunities: CrmOpportunityRow[]) {
  const active = opportunities.filter((o) => o.status !== "lost");
  const potentialRevenue = active.reduce((sum, o) => sum + (o.estimateMinRub + o.estimateMaxRub) / 2, 0);
  const minProfit = active.reduce((sum, o) => sum + (o.estimateMinRub * o.marginMinPercent) / 100, 0);
  const maxProfit = active.reduce((sum, o) => sum + (o.estimateMaxRub * o.marginMaxPercent) / 100, 0);
  return {
    potentialRevenue: Math.round(potentialRevenue),
    minProfit: Math.round(minProfit),
    maxProfit: Math.round(maxProfit),
    avgProfit: Math.round((minProfit + maxProfit) / 2)
  };
}
