import { notFound } from "next/navigation";
import { CurrentRunClient } from "./CurrentRunClient";
import { getCommittedEvents, getJob } from "../../../lib/controlTowerApi";

type Props = {
  params: Promise<{ jobId: string }>;
};

export default async function DiagnosticJobPage({ params }: Props) {
  const { jobId } = await params;
  let representation;
  try {
    representation = await getJob(jobId);
  } catch {
    notFound();
  }
  const replay = await getCommittedEvents(jobId, 0);

  return <CurrentRunClient initialEvents={replay.events} initialJob={representation} />;
}
