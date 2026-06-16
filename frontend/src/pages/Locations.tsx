/**
 * Locations page — tree browse and management for the location hierarchy.
 *
 * Delegates all rendering and CRUD logic to the shared TreeBrowser component,
 * parameterised with resource="locations".
 */
import { PageShell } from "../components/PageShell";
import { TreeBrowser } from "../components/TreeBrowser";

export function Locations() {
  return (
    <PageShell title="Locations">
      <TreeBrowser resource="locations" label="Location" />
    </PageShell>
  );
}
