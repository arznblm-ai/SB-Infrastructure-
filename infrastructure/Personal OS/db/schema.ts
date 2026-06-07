import { integer, sqliteTable, text } from "drizzle-orm/sqlite-core";

export const clients = sqliteTable("clients", {
  id: integer("id").primaryKey({ autoIncrement: true }),
  name: text("name").notNull(),
  contact: text("contact"),
  status: text("status", { enum: ["active", "lead", "paused", "archived"] }).notNull().default("active"),
  notes: text("notes"),
  createdAt: text("created_at").notNull()
});

export const accounts = sqliteTable("accounts", {
  id: integer("id").primaryKey({ autoIncrement: true }),
  name: text("name").notNull(),
  type: text("type", { enum: ["cash", "bank", "card", "crypto", "reserve"] }).notNull(),
  balanceRub: integer("balance_rub").notNull().default(0),
  referenceNote: text("reference_note"),
  updatedAt: text("updated_at").notNull()
});

export const confirmedProjects = sqliteTable("confirmed_projects", {
  id: integer("id").primaryKey({ autoIncrement: true }),
  clientId: integer("client_id").references(() => clients.id),
  name: text("name").notNull(),
  revenueRub: integer("revenue_rub").notNull(),
  taxRate: integer("tax_rate").notNull().default(12),
  expectedMonth: text("expected_month").notNull(),
  expectedDate: text("expected_date").notNull(),
  includeInForecast: integer("include_in_forecast").notNull().default(1),
  status: text("status", { enum: ["received", "expected", "in_work", "delayed", "risk", "done"] }).notNull(),
  notes: text("notes")
});

export const crmOpportunities = sqliteTable("crm_opportunities", {
  id: integer("id").primaryKey({ autoIncrement: true }),
  clientId: integer("client_id").references(() => clients.id),
  name: text("name").notNull(),
  estimateMinRub: integer("estimate_min_rub").notNull(),
  estimateMaxRub: integer("estimate_max_rub").notNull(),
  marginMinPercent: integer("margin_min_percent").notNull().default(40),
  marginMaxPercent: integer("margin_max_percent").notNull().default(60),
  status: text("status", { enum: ["query", "tender", "proposal", "waiting", "risk", "lost"] }).notNull().default("query"),
  nextStep: text("next_step"),
  nextContactDate: text("next_contact_date"),
  notes: text("notes")
});

export const obligations = sqliteTable("obligations", {
  id: integer("id").primaryKey({ autoIncrement: true }),
  label: text("label").notNull(),
  type: text("type", { enum: ["business_debt", "personal_debt", "tax", "monthly_expense", "one_time_expense"] }).notNull(),
  amountRub: integer("amount_rub").notNull(),
  dueMonth: text("due_month").notNull(),
  dueDate: text("due_date").notNull(),
  status: text("status", { enum: ["planned", "paid", "paused"] }).notNull().default("planned"),
  notes: text("notes")
});

export type Client = typeof clients.$inferSelect;
export type Account = typeof accounts.$inferSelect;
export type ConfirmedProject = typeof confirmedProjects.$inferSelect;
export type CrmOpportunity = typeof crmOpportunities.$inferSelect;
export type Obligation = typeof obligations.$inferSelect;
