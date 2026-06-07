DROP TABLE IF EXISTS `payments`;
--> statement-breakpoint
DROP TABLE IF EXISTS `projects`;
--> statement-breakpoint
DROP TABLE IF EXISTS `expenses`;
--> statement-breakpoint
DROP TABLE IF EXISTS `crm_opportunities`;
--> statement-breakpoint
DROP TABLE IF EXISTS `confirmed_projects`;
--> statement-breakpoint
DROP TABLE IF EXISTS `obligations`;
--> statement-breakpoint
DROP TABLE IF EXISTS `accounts`;
--> statement-breakpoint
CREATE TABLE `accounts` (
  `id` integer PRIMARY KEY AUTOINCREMENT NOT NULL,
  `name` text NOT NULL,
  `type` text NOT NULL,
  `balance_rub` integer DEFAULT 0 NOT NULL,
  `reference_note` text,
  `updated_at` text NOT NULL
);
--> statement-breakpoint
CREATE TABLE `confirmed_projects` (
  `id` integer PRIMARY KEY AUTOINCREMENT NOT NULL,
  `client_id` integer,
  `name` text NOT NULL,
  `revenue_rub` integer NOT NULL,
  `tax_rate` integer DEFAULT 12 NOT NULL,
  `expected_month` text NOT NULL,
  `expected_date` text NOT NULL,
  `include_in_forecast` integer DEFAULT 1 NOT NULL,
  `status` text NOT NULL,
  `notes` text,
  FOREIGN KEY (`client_id`) REFERENCES `clients`(`id`) ON UPDATE no action ON DELETE no action
);
--> statement-breakpoint
CREATE TABLE `crm_opportunities` (
  `id` integer PRIMARY KEY AUTOINCREMENT NOT NULL,
  `client_id` integer,
  `name` text NOT NULL,
  `estimate_min_rub` integer NOT NULL,
  `estimate_max_rub` integer NOT NULL,
  `margin_min_percent` integer DEFAULT 40 NOT NULL,
  `margin_max_percent` integer DEFAULT 60 NOT NULL,
  `status` text DEFAULT 'query' NOT NULL,
  `next_step` text,
  `next_contact_date` text,
  `notes` text,
  FOREIGN KEY (`client_id`) REFERENCES `clients`(`id`) ON UPDATE no action ON DELETE no action
);
--> statement-breakpoint
CREATE TABLE `obligations` (
  `id` integer PRIMARY KEY AUTOINCREMENT NOT NULL,
  `label` text NOT NULL,
  `type` text NOT NULL,
  `amount_rub` integer NOT NULL,
  `due_month` text NOT NULL,
  `due_date` text NOT NULL,
  `status` text DEFAULT 'planned' NOT NULL,
  `notes` text
);
