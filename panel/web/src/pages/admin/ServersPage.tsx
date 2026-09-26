import {
  Anchor, Badge, Button, Group, Modal, NumberInput, PasswordInput, ScrollArea, Stack, Switch, Table, Text, Textarea,
  TextInput, Title,
} from "@mantine/core";
import { useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router";
import { api } from "../../api/client";
import { keys, useAction, useServers } from "../../api/hooks";
import type { ServerInfo } from "../../api/types";
import { ErrorAlert } from "../../components/ErrorAlert";
import { JobStatus } from "../../components/JobStatus";
import { formatDateTime } from "../../lib/format";

const EMPTY = { name: "", host: "", ssh_port: 22, ssh_user: "root", ssh_password: "", ssh_private_key: "", enabled_for_users: true };

export default function ServersPage() {
  const { t, i18n } = useTranslation();
  const servers = useServers();
  const [adding, setAdding] = useState(false);
  const [form, setForm] = useState(EMPTY);
  const [jobId, setJobId] = useState<number | null>(null);
  const add = useAction(
    () =>
      api<{ server: ServerInfo; job_id: number }>("/api/admin/servers", {
        method: "POST",
        body: {
          ...form,
          ssh_password: form.ssh_password || null,
          ssh_private_key: form.ssh_private_key || null,
        },
      }),
    [keys.servers],
    (res) => {
      setAdding(false);
      setForm(EMPTY);
      setJobId(res.job_id);
    },
  );

  function submit(e: FormEvent) {
    e.preventDefault();
    add.mutate();
  }

  return (
    <Stack>
      <Group justify="space-between">
        <Title order={2}>{t("admin.servers")}</Title>
        <Button onClick={() => setAdding(true)}>{t("admin.add_server")}</Button>
      </Group>
      <JobStatus jobId={jobId} />
      <ErrorAlert error={servers.error} />
      {servers.data?.length === 0 && <Text c="dimmed">{t("admin.no_servers")}</Text>}
      {!!servers.data?.length && (
        <ScrollArea>
          <Table striped highlightOnHover miw={640}>
            <Table.Thead>
              <Table.Tr>
                <Table.Th>{t("admin.server_name")}</Table.Th>
                <Table.Th>{t("admin.host")}</Table.Th>
                <Table.Th>{t("admin.containers")}</Table.Th>
                <Table.Th>{t("admin.configs_count")}</Table.Th>
                <Table.Th>{t("admin.last_ok")}</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {servers.data.map((s) => (
                <Table.Tr key={s.id}>
                  <Table.Td>
                    <Anchor component={Link} to={`/admin/servers/${s.id}`}>
                      {s.name}
                    </Anchor>
                    {!s.enabled_for_users && (
                      <Badge ml="xs" size="xs" color="gray">
                        off
                      </Badge>
                    )}
                  </Table.Td>
                  <Table.Td>{s.host}</Table.Td>
                  <Table.Td>
                    <Group gap={4}>
                      {s.containers.map((c) => (
                        <Badge key={c.container} variant="light">
                          {c.title}
                        </Badge>
                      ))}
                    </Group>
                  </Table.Td>
                  <Table.Td>{s.configs_count}</Table.Td>
                  <Table.Td>
                    <Text size="sm">{formatDateTime(s.last_ok_at, i18n.language)}</Text>
                    {s.last_error && (
                      <Text size="xs" c="red">
                        {s.last_error}
                      </Text>
                    )}
                  </Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        </ScrollArea>
      )}

      <Modal opened={adding} onClose={() => setAdding(false)} title={t("admin.add_server")} size="lg">
        <form onSubmit={submit}>
          <Stack>
            <ErrorAlert error={add.error} />
            <TextInput label={t("admin.server_name")} value={form.name} required
              onChange={(e) => setForm({ ...form, name: e.currentTarget.value })} />
            <Group grow>
              <TextInput label={t("admin.host")} value={form.host} required
                onChange={(e) => setForm({ ...form, host: e.currentTarget.value.trim() })} />
              <NumberInput label={t("admin.ssh_port")} value={form.ssh_port} min={1} max={65535} allowDecimal={false}
                onChange={(v) => setForm({ ...form, ssh_port: Number(v) || 22 })} />
            </Group>
            <TextInput label={t("admin.ssh_user")} value={form.ssh_user} required
              onChange={(e) => setForm({ ...form, ssh_user: e.currentTarget.value.trim() })} />
            <PasswordInput label={t("admin.ssh_password")} value={form.ssh_password} autoComplete="off"
              onChange={(e) => setForm({ ...form, ssh_password: e.currentTarget.value })} />
            <Textarea label={t("admin.ssh_key")} value={form.ssh_private_key} autosize minRows={2} maxRows={6}
              styles={{ input: { fontFamily: "monospace", fontSize: 12 } }}
              onChange={(e) => setForm({ ...form, ssh_private_key: e.currentTarget.value })} />
            <Switch label={t("admin.enabled_for_users")} checked={form.enabled_for_users}
              onChange={(e) => setForm({ ...form, enabled_for_users: e.currentTarget.checked })} />
            <Group justify="flex-end">
              <Button type="submit" loading={add.isPending}>
                {t("admin.add_server")}
              </Button>
            </Group>
          </Stack>
        </form>
      </Modal>
    </Stack>
  );
}
