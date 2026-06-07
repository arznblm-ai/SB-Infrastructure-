import fs from "node:fs";
import path from "node:path";
import { DatabaseSync } from "node:sqlite";

const cwd = process.cwd();
const dbPath = path.join(cwd, "data", "personal-os.sqlite");
const backupsDir = path.join(cwd, "data", "backups");
fs.mkdirSync(backupsDir, { recursive: true });

if (!fs.existsSync(dbPath)) {
  console.log("No SQLite DB found. Nothing to backup.");
  process.exit(0);
}

const stamp = new Date().toISOString().replace(/[:.]/g, "-");
const target = path.join(backupsDir, `personal-os-${stamp}.sqlite`);
const quoteSql = (value: string) => `'${value.replace(/'/g, "''")}'`;

const db = new DatabaseSync(dbPath);
try {
  db.exec("PRAGMA busy_timeout = 5000;");
  db.exec("PRAGMA wal_checkpoint(TRUNCATE);");
  db.exec(`VACUUM INTO ${quoteSql(target)};`);
} finally {
  db.close();
}

console.log(`Backup created: ${target}`);
