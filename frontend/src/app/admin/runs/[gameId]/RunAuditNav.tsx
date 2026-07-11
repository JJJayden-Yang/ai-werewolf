import Link from "next/link";

type RunAuditNavProps = {
  gameId: string;
};

export function RunAuditNav({ gameId }: RunAuditNavProps) {
  const encoded = encodeURIComponent(gameId);

  return (
    <nav className="audit-tabs" aria-label="Run audit tabs">
      <Link href={`/admin/runs/${encoded}/timeline`}>Timeline</Link>
      <Link href={`/admin/runs/${encoded}/events`}>Events</Link>
      <Link href={`/admin/runs/${encoded}/raw`}>Raw</Link>
      <Link href={`/admin/runs/${encoded}/belief`}>Belief</Link>
      <Link href={`/admin/runs/${encoded}/network`}>Network</Link>
      <Link href={`/admin/runs/${encoded}/decisions`}>Decisions</Link>
      <Link href={`/admin/runs/${encoded}/context`}>Context</Link>
      <Link href={`/admin/runs/${encoded}/errors`}>Errors</Link>
    </nav>
  );
}
