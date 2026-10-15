import { ScrollArea, Stack, Table, Title } from "@mantine/core";
import { useTranslation } from "react-i18next";
import { useAdmins } from "../../api/hooks";
import { ErrorAlert } from "../../components/ErrorAlert";
import { ResetKeyButton } from "../../components/ResetKeyButton";
import { formatDateTime } from "../../lib/format";

export default function AdminsPage() {
  const { t, i18n } = useTranslation();
  const admins = useAdmins();
  return (
    <Stack>
      <Title order={2}>{t("admin.admins")}</Title>
      <ErrorAlert error={admins.error} />
      {admins.data && (
        <ScrollArea>
          <Table striped miw={560}>
            <Table.Thead>
              <Table.Tr>
                <Table.Th>{t("auth.login")}</Table.Th>
                <Table.Th>{t("admin.created_at")}</Table.Th>
                <Table.Th>{t("common.actions")}</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {admins.data.map((a) => (
                <Table.Tr key={a.id}>
                  <Table.Td>{a.login}</Table.Td>
                  <Table.Td>{formatDateTime(a.created_at, i18n.language)}</Table.Td>
                  <Table.Td>
                    <ResetKeyButton accountId={a.id} name={a.login} />
                  </Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        </ScrollArea>
      )}
    </Stack>
  );
}
