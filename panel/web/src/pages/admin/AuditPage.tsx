import { Button, ScrollArea, Stack, Table, Text, Title } from "@mantine/core";
import { useInfiniteQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { api } from "../../api/client";
import type { AuditEntry } from "../../api/types";
import { ErrorAlert } from "../../components/ErrorAlert";
import { formatDateTime } from "../../lib/format";

const PAGE = 100;

export default function AuditPage() {
  const { t, i18n } = useTranslation();
  const audit = useInfiniteQuery({
    queryKey: ["admin", "audit"],
    queryFn: ({ pageParam }) =>
      api<AuditEntry[]>(`/api/admin/audit?limit=${PAGE}${pageParam ? `&before=${pageParam}` : ""}`),
    initialPageParam: 0,
    getNextPageParam: (last) => (last.length === PAGE ? last[last.length - 1].id : undefined),
  });
  const entries = audit.data?.pages.flat() ?? [];

  return (
    <Stack>
      <Title order={2}>{t("admin.audit")}</Title>
      <ErrorAlert error={audit.error} />
      <ScrollArea>
        <Table striped miw={640}>
          <Table.Thead>
            <Table.Tr>
              <Table.Th>{t("admin.time")}</Table.Th>
              <Table.Th>{t("admin.actor")}</Table.Th>
              <Table.Th>{t("admin.action")}</Table.Th>
              <Table.Th>{t("admin.target")}</Table.Th>
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {entries.map((e) => (
              <Table.Tr key={e.id}>
                <Table.Td>{formatDateTime(e.ts, i18n.language)}</Table.Td>
                <Table.Td>{e.actor}</Table.Td>
                <Table.Td>{e.action}</Table.Td>
                <Table.Td>
                  <Text size="sm">{e.target}</Text>
                  {Object.keys(e.details).length > 0 && (
                    <Text size="xs" c="dimmed">
                      {JSON.stringify(e.details)}
                    </Text>
                  )}
                </Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      </ScrollArea>
      {audit.hasNextPage && (
        <Button variant="light" onClick={() => void audit.fetchNextPage()} loading={audit.isFetchingNextPage}>
          {t("admin.load_more")}
        </Button>
      )}
    </Stack>
  );
}
