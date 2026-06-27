/**
 * ExportMenu — reusable "Export ▾" dropdown menu for triggering file downloads.
 *
 * Each entity produces two options (CSV / JSON). The download is triggered via a
 * programmatic same-origin anchor click so the session cookie is included
 * automatically — no manual auth header needed.
 *
 * Pure utilities (`exportUrl`, `triggerDownload`, entity/format types) live in
 * `./exportUtils`.
 */

import { Menu, Button } from "@mantine/core";
import { Download } from "react-feather";
import { useTranslation } from "react-i18next";
import {
  type ExportEntity,
  type ExportFormat,
  exportUrl,
  triggerDownload,
} from "./exportUtils";

export interface ExportMenuProps {
  /** The entity to export. */
  entity: ExportEntity;
  /** Optional override for the data-testid on the trigger button. */
  "data-testid"?: string;
}

/**
 * "Export ▾" dropdown offering CSV and JSON download options for `entity`.
 */
export function ExportMenu({ entity, "data-testid": testId }: ExportMenuProps) {
  const { t } = useTranslation("export");

  function handleExport(format: ExportFormat) {
    triggerDownload(exportUrl(entity, format));
  }

  return (
    <Menu position="bottom-end" withArrow>
      <Menu.Target>
        <Button
          variant="light"
          leftSection={<Download size={14} />}
          data-testid={testId ?? `export-menu-${entity}`}
        >
          {t(`entityLabel.${entity}`, { defaultValue: t("menuLabel") })}
        </Button>
      </Menu.Target>
      <Menu.Dropdown>
        <Menu.Item
          onClick={() => handleExport("csv")}
          data-testid={`export-csv-${entity}`}
        >
          {t("csv")}
        </Menu.Item>
        <Menu.Item
          onClick={() => handleExport("json")}
          data-testid={`export-json-${entity}`}
        >
          {t("json")}
        </Menu.Item>
      </Menu.Dropdown>
    </Menu>
  );
}
