import { Button, Group, ScrollArea, Table, Text } from "@mantine/core";
import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";
import type { Config } from "../api/types";
import { formatBytes } from "../lib/format";
import { StatusBadge } from "./StatusBadge";

type Props = {
  configs: Config[];
  onShow: (c: Config) => void;
  onBlock: (c: Config) => void;
  onUnblock: (c: Config) => void;
  onDelete: (c: Config) => void;
  /** Whether a config's unblock button is offered (users may lift only their own blocks). */
  canUnblock: (c: Config) => boolean;
  showDisabled?: boolean;
  showOwner?: boolean;
  extraActions?: (c: Config) => ReactNode;
};

export function ConfigsTable({
  configs, onShow, onBlock, onUnblock, onDelete, canUnblock, showDisabled, showOwner, extraActions,
}: Props) {
  const { t } = useTranslation();
  if (configs.length === 0) return <Text c="dimmed">{t("dashboard.no_configs")}</Text>;
  return (
    <ScrollArea>
      <Table striped highlightOnHover miw={640}>
        <Table.Thead>
          <Table.Tr>
            <Table.Th>{t("dashboard.col_name")}</Table.Th>
            <Table.Th>{t("dashboard.col_server")}</Table.Th>
            <Table.Th>{t("dashboard.col_protocol")}</Table.Th>
            <Table.Th>{t("dashboard.col_status")}</Table.Th>
            <Table.Th>{t("dashboard.col_traffic")}</Table.Th>
            <Table.Th>{t("common.actions")}</Table.Th>
          </Table.Tr>
        </Table.Thead>
        <Table.Tbody>
          {configs.map((c) => (
            <Table.Tr key={c.id}>
              <Table.Td>
                <Text size="sm">{c.name}</Text>
                {showOwner && c.can_render === false && (
                  <Text size="xs" c="dimmed">
                    {t("admin.imported")}
                  </Text>
                )}
              </Table.Td>
              <Table.Td>{c.server_name}</Table.Td>
              <Table.Td>{c.protocol}</Table.Td>
              <Table.Td>
                <StatusBadge status={c.status} blockedBy={c.blocked_by} />
              </Table.Td>
              <Table.Td>
                <Text size="xs">
                  ↓ {formatBytes(c.traffic.rx)} · ↑ {formatBytes(c.traffic.tx)}
                </Text>
              </Table.Td>
              <Table.Td>
                <Group gap={4} wrap="nowrap">
                  <Button size="compact-xs" variant="light" onClick={() => onShow(c)} disabled={showDisabled}>
                    {t("common.show")}
                  </Button>
                  {c.blocked_by === null ? (
                    <Button size="compact-xs" variant="light" color="orange" onClick={() => onBlock(c)}>
                      {t("common.block")}
                    </Button>
                  ) : (
                    canUnblock(c) && (
                      <Button size="compact-xs" variant="light" color="teal" onClick={() => onUnblock(c)}>
                        {t("common.unblock")}
                      </Button>
                    )
                  )}
                  <Button size="compact-xs" variant="light" color="red" onClick={() => onDelete(c)}>
                    {t("common.delete")}
                  </Button>
                  {extraActions?.(c)}
                </Group>
              </Table.Td>
            </Table.Tr>
          ))}
        </Table.Tbody>
      </Table>
    </ScrollArea>
  );
}
