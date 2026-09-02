import { CreateDiagnosticJobForm } from "./CreateDiagnosticJobForm";
import { getCatalog } from "../../../lib/controlTowerApi";

export default async function NewDiagnosticJobPage() {
  const catalog = await getCatalog();
  return (
    <section className="stack">
      <div>
        <p className="eyebrow">New job</p>
        <h1>Create foundation diagnostic job</h1>
        <p className="meta">Choose registered inputs only. This page does not submit executable details.</p>
      </div>
      <CreateDiagnosticJobForm catalog={catalog} />
    </section>
  );
}
