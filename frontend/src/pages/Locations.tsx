/**
 * Locations page — tree browse and management for the location hierarchy.
 *
 * Delegates all rendering and CRUD logic to the shared TreeBrowser component,
 * parameterised with resource="locations".
 */
import { useTranslation } from "react-i18next";
import { PageShell } from "../components/PageShell";
import { TreeBrowser } from "../components/TreeBrowser";
import { ExportMenu } from "../components/ExportMenu";

export function Locations() {
  const { t: tNav } = useTranslation("nav");
  const { t: tLoc } = useTranslation("locations");
  return (
    <PageShell
      title={tNav("locations")}
      subtitle={tLoc("page.subtitle")}
      actions={<ExportMenu entity="locations" />}
    >
      <TreeBrowser resource="locations" />
    </PageShell>
  );
}
