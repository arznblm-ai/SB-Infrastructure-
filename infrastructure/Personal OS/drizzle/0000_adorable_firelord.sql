CREATE TABLE `accounts` (
	`id` integer PRIMARY KEY AUTOINCREMENT NOT NULL,
	`name` text NOT NULL,
	`type` text NOT NULL,
	`currency` text DEFAULT 'USD' NOT NULL,
	`balance` integer NOT NULL,
	`updated_at` text NOT NULL
);
--> statement-breakpoint
CREATE TABLE `clients` (
	`id` integer PRIMARY KEY AUTOINCREMENT NOT NULL,
	`name` text NOT NULL,
	`contact` text,
	`status` text DEFAULT 'active' NOT NULL,
	`notes` text,
	`created_at` text NOT NULL
);
--> statement-breakpoint
CREATE TABLE `expenses` (
	`id` integer PRIMARY KEY AUTOINCREMENT NOT NULL,
	`label` text NOT NULL,
	`category` text NOT NULL,
	`amount` integer NOT NULL,
	`currency` text DEFAULT 'USD' NOT NULL,
	`cadence` text DEFAULT 'monthly' NOT NULL,
	`status` text DEFAULT 'planned' NOT NULL,
	`due_date` text NOT NULL,
	`paid_date` text,
	`notes` text
);
--> statement-breakpoint
CREATE TABLE `payments` (
	`id` integer PRIMARY KEY AUTOINCREMENT NOT NULL,
	`project_id` integer,
	`client_id` integer,
	`label` text NOT NULL,
	`amount` integer NOT NULL,
	`currency` text DEFAULT 'USD' NOT NULL,
	`status` text NOT NULL,
	`expected_date` text NOT NULL,
	`paid_date` text,
	`probability` real DEFAULT 0.8 NOT NULL,
	`scenario` text DEFAULT 'base' NOT NULL,
	`notes` text,
	FOREIGN KEY (`project_id`) REFERENCES `projects`(`id`) ON UPDATE no action ON DELETE no action,
	FOREIGN KEY (`client_id`) REFERENCES `clients`(`id`) ON UPDATE no action ON DELETE no action
);
--> statement-breakpoint
CREATE TABLE `projects` (
	`id` integer PRIMARY KEY AUTOINCREMENT NOT NULL,
	`client_id` integer,
	`name` text NOT NULL,
	`status` text DEFAULT 'active' NOT NULL,
	`type` text NOT NULL,
	`currency` text DEFAULT 'USD' NOT NULL,
	`contract_value` integer DEFAULT 0 NOT NULL,
	`start_date` text NOT NULL,
	`end_date` text,
	`health` text DEFAULT 'green' NOT NULL,
	`notes` text,
	FOREIGN KEY (`client_id`) REFERENCES `clients`(`id`) ON UPDATE no action ON DELETE no action
);
