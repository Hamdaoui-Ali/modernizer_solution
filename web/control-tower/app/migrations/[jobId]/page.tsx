import { MigrationCockpit } from "./MigrationCockpit";

export const metadata = {
  title: "Migration Cockpit | Control Tower",
};

export default async function MigrationCockpitPage({
  params,
}: {
  params: Promise<{ jobId: string }>;
}) {
  const { jobId } = await params;

  return (
    <section>
      <div />
      <MigrationCockpit jobId={jobId} />
    </section>
  );
}
