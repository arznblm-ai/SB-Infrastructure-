"use server";

import { revalidatePath } from "next/cache";
import { all, run } from "@/db/client";

const CLIENT_STATUS = new Set(["active", "lead", "paused", "archived"]);
const ACCOUNT_TYPE = new Set(["cash", "bank", "card", "crypto", "reserve"]);
const PROJECT_STATUS = new Set(["received", "expected", "in_work", "delayed", "risk", "done"]);
const CRM_STATUS = new Set(["query", "tender", "proposal", "waiting", "risk", "lost"]);
const OBLIGATION_TYPE = new Set(["business_debt", "personal_debt", "tax", "monthly_expense", "one_time_expense"]);
const OBLIGATION_STATUS = new Set(["planned", "paid", "paused"]);

function asOptionalNumber(v: FormDataEntryValue | null) {
  if (v === null || v === "") return null;
  const out = Number(v);
  return Number.isFinite(out) ? out : null;
}

function n(v: FormDataEntryValue | null, d = 0) {
  const out = asOptionalNumber(v);
  return out === null ? d : out;
}

function s(v: FormDataEntryValue | null, d = "") {
  if (v === null) return d;
  const out = String(v).trim();
  return out.length ? out : d;
}

function sn(v: FormDataEntryValue | null) {
  const out = s(v);
  return out.length ? out : null;
}

function done() {
  revalidatePath("/");
}

function assert(condition: boolean, message: string) {
  if (!condition) throw new Error(message);
}

function requireName(name: string, field: string) {
  assert(name.trim().length > 0, `${field}: empty`);
}

function requireNonNegative(value: number, field: string) {
  assert(Number.isFinite(value) && value >= 0, `${field}: must be non-negative`);
}

function ensureInSet(value: string, allowed: Set<string>, field: string) {
  assert(allowed.has(value), `${field}: invalid value`);
}

function validMonth(month: string) {
  if (!/^\d{4}-\d{2}$/.test(month)) return false;
  const [y, m] = month.split("-").map(Number);
  return y >= 2000 && m >= 1 && m <= 12;
}

function validDate(dateStr: string) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(dateStr)) return false;
  const [y, m, d] = dateStr.split("-").map(Number);
  const date = new Date(Date.UTC(y, m - 1, d));
  return date.getUTCFullYear() === y && date.getUTCMonth() === m - 1 && date.getUTCDate() === d;
}

export async function saveClient(formData: FormData) {
  const id = n(formData.get("id"), 0);
  const payload = {
    name: s(formData.get("name")),
    contact: sn(formData.get("contact")),
    status: s(formData.get("status"), "active"),
    notes: sn(formData.get("notes"))
  };
  requireName(payload.name, "client.name");
  ensureInSet(payload.status, CLIENT_STATUS, "client.status");

  if (id > 0) {
    run("update clients set name=:name, contact=:contact, status=:status, notes=:notes where id=:id", { id, ...payload });
  } else {
    run(
      "insert into clients (name, contact, status, notes, created_at) values (:name, :contact, :status, :notes, :createdAt)",
      { ...payload, createdAt: new Date().toISOString().slice(0, 10) }
    );
  }
  done();
}

export async function deleteClient(formData: FormData) {
  const id = n(formData.get("id"));
  if (id <= 0) {
    done();
    return;
  }

  const links = all<{ projectCount: number; crmCount: number }>(
    `select
      (select count(*) from confirmed_projects where client_id = :id) as projectCount,
      (select count(*) from crm_opportunities where client_id = :id) as crmCount`,
    { id }
  )[0];
  const hasLinks = (links?.projectCount ?? 0) + (links?.crmCount ?? 0) > 0;

  if (hasLinks) {
    run("update clients set status='archived' where id=:id", { id });
    done();
    return;
  }

  try {
    run("delete from clients where id=:id", { id });
  } catch {
    // Defensive fallback for race conditions when related rows appear between checks.
    run("update clients set status='archived' where id=:id", { id });
  }
  done();
}

export async function saveAccount(formData: FormData) {
  const id = n(formData.get("id"), 0);
  const payload = {
    name: s(formData.get("name")),
    type: s(formData.get("type"), "bank").toLowerCase(),
    balanceRub: n(formData.get("balanceRub")),
    referenceNote: sn(formData.get("referenceNote")),
    updatedAt: new Date().toISOString().slice(0, 10)
  };
  requireName(payload.name, "account.name");
  ensureInSet(payload.type, ACCOUNT_TYPE, "account.type");
  requireNonNegative(payload.balanceRub, "account.balanceRub");

  if (id > 0) {
    run(
      "update accounts set name=:name, type=:type, balance_rub=:balanceRub, reference_note=:referenceNote, updated_at=:updatedAt where id=:id",
      { id, ...payload }
    );
  } else {
    run(
      "insert into accounts (name, type, balance_rub, reference_note, updated_at) values (:name, :type, :balanceRub, :referenceNote, :updatedAt)",
      payload
    );
  }
  done();
}

export async function deleteAccount(formData: FormData) {
  run("delete from accounts where id=:id", { id: n(formData.get("id")) });
  done();
}

export async function saveConfirmedProject(formData: FormData) {
  const id = n(formData.get("id"), 0);
  const includeInForecast = formData.has("includeInForecast") ? 1 : 0;
  const payload = {
    clientId: n(formData.get("clientId"), 0) || null,
    name: s(formData.get("name")),
    revenueRub: n(formData.get("revenueRub")),
    taxRate: n(formData.get("taxRate"), 12),
    expectedMonth: s(formData.get("expectedMonth")),
    expectedDate: s(formData.get("expectedDate")),
    includeInForecast,
    status: s(formData.get("status"), "expected"),
    notes: sn(formData.get("notes"))
  };
  requireName(payload.name, "project.name");
  requireNonNegative(payload.revenueRub, "project.revenueRub");
  assert(Number.isFinite(payload.taxRate) && payload.taxRate >= 0 && payload.taxRate <= 30, "project.taxRate: 0..30");
  assert(validMonth(payload.expectedMonth), "project.expectedMonth: YYYY-MM");
  assert(validDate(payload.expectedDate), "project.expectedDate: YYYY-MM-DD");
  ensureInSet(payload.status, PROJECT_STATUS, "project.status");

  if (id > 0) {
    run(
      `update confirmed_projects
       set client_id=:clientId, name=:name, revenue_rub=:revenueRub, tax_rate=:taxRate, expected_month=:expectedMonth,
           expected_date=:expectedDate, include_in_forecast=:includeInForecast, status=:status, notes=:notes
       where id=:id`,
      { id, ...payload }
    );
  } else {
    run(
      `insert into confirmed_projects
       (client_id, name, revenue_rub, tax_rate, expected_month, expected_date, include_in_forecast, status, notes)
       values (:clientId, :name, :revenueRub, :taxRate, :expectedMonth, :expectedDate, :includeInForecast, :status, :notes)`,
      payload
    );
  }
  done();
}

export async function deleteConfirmedProject(formData: FormData) {
  run("delete from confirmed_projects where id=:id", { id: n(formData.get("id")) });
  done();
}

export async function toggleConfirmedProject(formData: FormData) {
  run("update confirmed_projects set include_in_forecast=:include where id=:id", {
    id: n(formData.get("id")),
    include: n(formData.get("include"), 1)
  });
  done();
}

export async function saveCrmOpportunity(formData: FormData) {
  const id = n(formData.get("id"), 0);
  const payload = {
    clientId: n(formData.get("clientId"), 0) || null,
    name: s(formData.get("name")),
    estimateMinRub: n(formData.get("estimateMinRub")),
    estimateMaxRub: n(formData.get("estimateMaxRub")),
    marginMinPercent: n(formData.get("marginMinPercent"), 40),
    marginMaxPercent: n(formData.get("marginMaxPercent"), 60),
    status: s(formData.get("status"), "query"),
    nextStep: sn(formData.get("nextStep")),
    nextContactDate: sn(formData.get("nextContactDate")),
    notes: sn(formData.get("notes"))
  };
  requireName(payload.name, "crm.name");
  requireNonNegative(payload.estimateMinRub, "crm.estimateMinRub");
  requireNonNegative(payload.estimateMaxRub, "crm.estimateMaxRub");
  assert(payload.estimateMaxRub >= payload.estimateMinRub, "crm.estimateMaxRub >= estimateMinRub");
  assert(payload.marginMinPercent >= 0 && payload.marginMinPercent <= 100, "crm.marginMinPercent: 0..100");
  assert(payload.marginMaxPercent >= 0 && payload.marginMaxPercent <= 100, "crm.marginMaxPercent: 0..100");
  ensureInSet(payload.status, CRM_STATUS, "crm.status");
  if (payload.nextContactDate) {
    assert(validDate(payload.nextContactDate), "crm.nextContactDate: YYYY-MM-DD");
  }

  if (id > 0) {
    run(
      `update crm_opportunities
       set client_id=:clientId, name=:name, estimate_min_rub=:estimateMinRub, estimate_max_rub=:estimateMaxRub,
           margin_min_percent=:marginMinPercent, margin_max_percent=:marginMaxPercent, status=:status,
           next_step=:nextStep, next_contact_date=:nextContactDate, notes=:notes
       where id=:id`,
      { id, ...payload }
    );
  } else {
    run(
      `insert into crm_opportunities
       (client_id, name, estimate_min_rub, estimate_max_rub, margin_min_percent, margin_max_percent, status, next_step, next_contact_date, notes)
       values (:clientId, :name, :estimateMinRub, :estimateMaxRub, :marginMinPercent, :marginMaxPercent, :status, :nextStep, :nextContactDate, :notes)`,
      payload
    );
  }
  done();
}

export async function deleteCrmOpportunity(formData: FormData) {
  run("delete from crm_opportunities where id=:id", { id: n(formData.get("id")) });
  done();
}

export async function saveObligation(formData: FormData) {
  const id = n(formData.get("id"), 0);
  const payload = {
    label: s(formData.get("label")),
    type: s(formData.get("type"), "one_time_expense").toLowerCase(),
    amountRub: n(formData.get("amountRub")),
    dueMonth: s(formData.get("dueMonth")),
    dueDate: s(formData.get("dueDate")),
    status: s(formData.get("status"), "planned").toLowerCase(),
    notes: sn(formData.get("notes"))
  };
  requireName(payload.label, "obligation.label");
  ensureInSet(payload.type, OBLIGATION_TYPE, "obligation.type");
  ensureInSet(payload.status, OBLIGATION_STATUS, "obligation.status");
  requireNonNegative(payload.amountRub, "obligation.amountRub");
  assert(validMonth(payload.dueMonth), "obligation.dueMonth: YYYY-MM");
  assert(validDate(payload.dueDate), "obligation.dueDate: YYYY-MM-DD");

  if (id > 0) {
    run(
      `update obligations
       set label=:label, type=:type, amount_rub=:amountRub, due_month=:dueMonth, due_date=:dueDate, status=:status, notes=:notes
       where id=:id`,
      { id, ...payload }
    );
  } else {
    run(
      `insert into obligations
       (label, type, amount_rub, due_month, due_date, status, notes)
       values (:label, :type, :amountRub, :dueMonth, :dueDate, :status, :notes)`,
      payload
    );
  }
  done();
}

export async function deleteObligation(formData: FormData) {
  run("delete from obligations where id=:id", { id: n(formData.get("id")) });
  done();
}
