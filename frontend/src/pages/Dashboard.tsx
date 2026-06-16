/**
 * Dashboard — placeholder page for the root route ("/").
 *
 * Content will expand in later milestones.
 */
import { PageShell } from "../components/PageShell";
import { EmptyState } from "../components/EmptyState";

export function Dashboard() {
  return (
    <PageShell title="Dashboard">
      <EmptyState message="Your inventory dashboard will appear here." />
    </PageShell>
  );
}
