/**
 * Categories page — tree browse and management for the category hierarchy.
 *
 * Delegates all rendering and CRUD logic to the shared TreeBrowser component,
 * parameterised with resource="categories".
 */
import { PageShell } from "../components/PageShell";
import { TreeBrowser } from "../components/TreeBrowser";

export function Categories() {
  return (
    <PageShell title="Categories">
      <TreeBrowser resource="categories" label="Category" labelPlural="Categories" />
    </PageShell>
  );
}
