import { DatabaseSync, type SQLInputValue } from "node:sqlite";
import fs from "node:fs";
import path from "node:path";

const dbPath = path.join(process.cwd(), "data", "personal-os.sqlite");
fs.mkdirSync(path.dirname(dbPath), { recursive: true });

export const db = new DatabaseSync(dbPath);
db.exec("PRAGMA journal_mode = WAL;");
db.exec("PRAGMA foreign_keys = ON;");

export function all<T>(sql: string, params: Record<string, SQLInputValue> = {}) {
  return db.prepare(sql).all(params) as T[];
}

export function run(sql: string, params: Record<string, SQLInputValue> = {}) {
  return db.prepare(sql).run(params);
}
