import { getCockpitData } from "@/lib/finance";
import { RubCockpit } from "@/components/rub-cockpit";

export const dynamic = "force-dynamic";

export default async function Home() {
  const data = await getCockpitData();
  return (
    <main className="min-h-screen">
      <RubCockpit
        accounts={data.accounts.map((r) => ({ ...r }))}
        confirmedProjects={data.confirmedProjects.map((r) => ({ ...r }))}
        crmOpportunities={data.crmOpportunities.map((r) => ({ ...r }))}
        obligations={data.obligations.map((r) => ({ ...r }))}
      />
    </main>
  );
}
