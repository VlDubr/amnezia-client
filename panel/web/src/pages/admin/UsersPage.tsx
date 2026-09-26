import { Anchor, Button, Group, Modal, ScrollArea, Select, Stack, Table, Text, TextInput, Title } from "@mantine/core";
import { useDebouncedValue } from "@mantine/hooks";
import { useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router";
import { api } from "../../api/client";
import { useAction, useUsers } from "../../api/hooks";
import type { AdminUser } from "../../api/types";
import { ErrorAlert } from "../../components/ErrorAlert";
import { InviteKeyModal } from "../../components/InviteKeyModal";
import { StatusBadge } from "../../components/StatusBadge";
import { EMPTY_USER, UserFields, type UserFieldValues } from "../../components/UserFields";
import { formatBytes, formatDate } from "../../lib/format";

export default function UsersPage() {
  const { t, i18n } = useTranslation();
  const [q, setQ] = useState("");
  const [status, setStatus] = useState<string | null>(null);
  const [debouncedQ] = useDebouncedValue(q, 300);
  const params = new URLSearchParams();
  if (debouncedQ) params.set("q", debouncedQ);
  if (status) params.set("status", status);
  const users = useUsers(params);

  const [creating, setCreating] = useState(false);
  const [form, setForm] = useState<UserFieldValues>(EMPTY_USER);
  const [inviteKey, setInviteKey] = useState<string | null>(null);
  const create = useAction(
    () =>
      api<{ user: AdminUser; invite_key: string }>("/api/admin/users", {
        method: "POST",
        body: { display_name: form.display_name, max_configs: form.max_configs, expires_on: form.expires_on || null, note: form.note },
      }),
    [["admin", "users"]],
    (res) => {
      setCreating(false);
      setForm(EMPTY_USER);
      setInviteKey(res.invite_key);
    },
  );

  function submit(e: FormEvent) {
    e.preventDefault();
    create.mutate();
  }

  return (
    <Stack>
      <Group justify="space-between">
        <Title order={2}>{t("admin.users")}</Title>
        <Button onClick={() => setCreating(true)}>{t("admin.new_user")}</Button>
      </Group>
      <Group>
        <TextInput placeholder={t("common.search")} value={q} onChange={(e) => setQ(e.currentTarget.value)} />
        <Select
          placeholder={t("admin.filter_status")}
          clearable
          value={status}
          onChange={setStatus}
          data={["active", "blocked", "expired", "deleting"].map((s) => ({ value: s, label: t(`status.${s}`) }))}
        />
      </Group>
      <ErrorAlert error={users.error} />
      {users.data && users.data.length === 0 && <Text c="dimmed">{t("admin.no_users")}</Text>}
      {users.data && users.data.length > 0 && (
        <ScrollArea>
          <Table striped highlightOnHover miw={720}>
            <Table.Thead>
              <Table.Tr>
                <Table.Th>{t("admin.display_name")}</Table.Th>
                <Table.Th>{t("auth.login")}</Table.Th>
                <Table.Th>{t("dashboard.col_status")}</Table.Th>
                <Table.Th>{t("admin.expires_on")}</Table.Th>
                <Table.Th>{t("admin.configs_count")}</Table.Th>
                <Table.Th>{t("dashboard.col_traffic")}</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {users.data.map((u) => (
                <Table.Tr key={u.id}>
                  <Table.Td>
                    <Anchor component={Link} to={`/admin/users/${u.id}`}>
                      {u.display_name}
                    </Anchor>
                  </Table.Td>
                  <Table.Td>{u.login ?? <Text c="dimmed" size="sm">{t("admin.not_registered")}</Text>}</Table.Td>
                  <Table.Td>
                    <StatusBadge status={u.status} blockedBy={u.blocked_by === "admin" ? "admin" : null} />
                  </Table.Td>
                  <Table.Td>{formatDate(u.expires_on, i18n.language)}</Table.Td>
                  <Table.Td>{`${u.configs_count} / ${u.max_configs}`}</Table.Td>
                  <Table.Td>
                    <Text size="xs">
                      ↓ {formatBytes(u.traffic_total.rx)} · ↑ {formatBytes(u.traffic_total.tx)}
                    </Text>
                  </Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        </ScrollArea>
      )}

      <Modal opened={creating} onClose={() => setCreating(false)} title={t("admin.new_user")}>
        <form onSubmit={submit}>
          <Stack>
            <ErrorAlert error={create.error} />
            <UserFields value={form} onChange={setForm} />
            <Group justify="flex-end">
              <Button type="submit" loading={create.isPending}>
                {t("common.create")}
              </Button>
            </Group>
          </Stack>
        </form>
      </Modal>
      <InviteKeyModal inviteKey={inviteKey} onClose={() => setInviteKey(null)} />
    </Stack>
  );
}
