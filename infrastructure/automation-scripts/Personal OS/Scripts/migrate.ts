import fs from "node:fs";
import path from "node:path";
import { db, run } from "../db/client";

const migrationsDir = path.join(process.cwd(), "drizzle");

run(`
  create table if not exists __migrations (
    id integer primary key autoincrement,
    name text not null unique,
    applied_at text not null
  )
`);

const applied = new Set(
  db.prepare("select name from __migrations").all().map((row) => (row as { name: string }).name)
);

for (const file of fs.readdirSync(migrationsDir).filter((name) => name.endsWith(".sql")).sort()) {
  if (applied.has(file)) continue;

  const sql = fs
    .readFileSync(path.join(migrationsDir, file), "utf8")
    .split("--> statement-breakpoint")
    .map((statement) => statement.trim())
    .filter(Boolean);

  db.exec("begin");
  try {
    for (const statement of sql) {
      db.exec(statement);
    }
    run("insert into __migrations (name, applied_at) values (:name, :appliedAt)", {
      name: file,
      appliedAt: new Date().toISOString()
    });
    db.exec("commit");
    console.log(`Applied ${file}`);
  } catch (error) {
    db.exec("rollback");
    throw error;
  }
}
