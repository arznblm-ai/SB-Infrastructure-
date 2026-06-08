import fs from "node:fs";
import path from "node:path";
import { run } from "../db/client";

const dbPath = path.join(process.cwd(), "data", "personal-os.sqlite");

if (fs.existsSync(dbPath)) {
  run("delete from confirmed_projects");
  run("delete from crm_opportunities");
  run("delete from obligations");
  run("delete from accounts");
  run("delete from clients");
}

console.log("Database is empty and ready for real data input.");
