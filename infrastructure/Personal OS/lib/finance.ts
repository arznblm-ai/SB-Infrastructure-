import { all } from "@/db/client";
import type { AccountRow, ConfirmedProjectRow, CrmOpportunityRow, ObligationRow } from "@/lib/finance-math";

export async function getCockpitData() {
  const accounts = all<AccountRow>(`
    select
      id,
      name,
      type,
      balance_rub as balanceRub,
      reference_note as referenceNote,
      updated_at as updatedAt
    from accounts
    order by id
  `);

  const clients = all<{ id: number; name: string; contact: string | null; status: string; notes: string | null }>(`
    select id, name, contact, status, notes
    from clients
    order by name
  `);

  const confirmedProjects = all<ConfirmedProjectRow>(`
    select
      cp.id,
      cp.client_id as clientId,
      c.name as clientName,
      cp.name,
      cp.revenue_rub as revenueRub,
      cp.tax_rate as taxRate,
      cp.expected_month as expectedMonth,
      cp.expected_date as expectedDate,
      cp.include_in_forecast as includeInForecast,
      cp.status,
      cp.notes
    from confirmed_projects cp
    left join clients c on c.id = cp.client_id
    order by cp.expected_date
  `);

  const crmOpportunities = all<CrmOpportunityRow>(`
    select
      co.id,
      co.client_id as clientId,
      c.name as clientName,
      co.name,
      co.estimate_min_rub as estimateMinRub,
      co.estimate_max_rub as estimateMaxRub,
      co.margin_min_percent as marginMinPercent,
      co.margin_max_percent as marginMaxPercent,
      co.status,
      co.next_step as nextStep,
      co.next_contact_date as nextContactDate,
      co.notes
    from crm_opportunities co
    left join clients c on c.id = co.client_id
    order by co.id desc
  `);

  const obligations = all<ObligationRow>(`
    select
      id,
      label,
      type,
      amount_rub as amountRub,
      due_month as dueMonth,
      due_date as dueDate,
      status,
      notes
    from obligations
    order by due_date
  `);

  return {
    baseCurrency: "RUB" as const,
    accounts,
    clients,
    confirmedProjects,
    crmOpportunities,
    obligations
  };
}
