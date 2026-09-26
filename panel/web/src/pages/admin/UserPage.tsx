import { Anchor, Button, Card, Group, Modal, SimpleGrid, Stack, Text, Title } from "@mantine/core";
import { useEffect, useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { Link, useNavigate, useParams } from "react-router";
import { api } from "../../api/client";
import { keys, useAction, useServers, useTraffic, useUser } from "../../api/hooks";
import type { Config } from "../../api/types";
import { ConfigsTable } from "../../components/ConfigsTable";
import { confirmAction } from "../../components/confirm";
import { ErrorAlert } from "../../components/ErrorAlert";
import { InviteKeyModal } from "../../components/InviteKeyModal";
import { StatusBadge } from "../../components/StatusBadge";
import { TrafficChart } from "../../components/TrafficChart";
import { useShare } from "../../components/useShare";
import { UserFields, userBody, type UserFieldValues } from "../../components/UserFields";
import { formatBytes } from "../../lib/format";

/** А.1–А.7 for one user: profile, limits, block/delete, invite key, configs and traffic. */
export default function UserPage() {
  const { t } = useTranslation();
  const id = Number(useParams().id);
  const navigate = useNavigate();
  const user = useUser(id);
  const traffic = useTraffic(new URLSearchParams({ user_id: String(id) }));
  const servers = useServers();
  const share = useShare("/api/admin/configs");
  const refresh = [keys.user(id), ["admin", "users"], ["admin", "traffic"]];

  const [form, setForm] = useState<UserFieldValues | null>(null);
  useEffect(() => {
    if (user.data) {
      setForm({
        display_name: user.data.display_name,
        note: user.data.note,
        max_configs: user.data.max_configs,
        expires_on: user.data.expires_on ?? "",
      });
    }
  }, [user.data]);
  const [inviteKey, setInviteKey] = useState<string | null>(null);
  const [issuing, setIssuing] = useState(false);

  const save = useAction(() => api(`/api/admin/users/${id}`, { method: "PATCH", body: userBody(form!) }), refresh);
  const block = useAction(() => api(`/api/admin/users/${id}/block`, { method: "POST" }), refresh);
  const unblock = useAction(() => api(`/api/admin/users/${id}/unblock`, { method: "POST" }), refresh);
  const remove = useAction(() => api(`/api/admin/users/${id}`, { method: "DELETE" }), [["admin", "users"]], () =>
    navigate("/admin/users"),
  );
  const reissue = useAction(
    () => api<{ invite_key: string }>(`/api/admin/users/${id}/invite`, { method: "POST" }),
    refresh,
    (r) => setInviteKey(r.invite_key),
  );
  const issue = useAction(
    (v: { server_id: number; container: string }) =>
      api<Config>(`/api/admin/users/${id}/configs`, { method: "POST", body: v }),
    refresh,
    (created) => {
      setIssuing(false);
      void share.open(created.id);
    },
  );
  const cfgBlock = useAction((c: Config) => api(`/api/admin/configs/${c.id}/block`, { method: "POST" }), refresh);
  const cfgUnblock = useAction((c: Config) => api(`/api/admin/configs/${c.id}/unblock`, { method: "POST" }), refresh);
  const cfgDelete = useAction((c: Config) => api(`/api/admin/configs/${c.id}`, { method: "DELETE" }), refresh);

  const u = user.data;
  if (!u || !form) return <ErrorAlert error={user.error} />;

  function submit(e: FormEvent) {
    e.preventDefault();
    save.mutate();
  }

  return (
    <Stack>
      <Anchor component={Link} to="/admin/users" size="sm">
        ← {t("admin.users")}
      </Anchor>
      <Group justify="space-between" align="flex-start">
        <Stack gap={4}>
          <Title order={2}>{u.display_name}</Title>
          <Group gap="xs">
            <StatusBadge status={u.status} blockedBy={u.blocked_by === "admin" ? "admin" : null} />
            <Text size="sm" c="dimmed">
              {u.registered ? `${t("admin.registered")}: ${u.login}` : t("admin.not_registered")}
            </Text>
          </Group>
        </Stack>
        <Group gap="xs">
          {!u.registered && (
            <Button variant="light" onClick={() => confirmAction(t("admin.reissue_confirm"), () => reissue.mutate(), false)}>
              {t("admin.reissue_invite")}
            </Button>
          )}
          {u.blocked_by === "admin" ? (
            <Button variant="light" color="teal" onClick={() => unblock.mutate()}>
              {t("common.unblock")}
            </Button>
          ) : (
            <Button
              variant="light"
              color="orange"
              onClick={() => confirmAction(t("admin.block_user", { name: u.display_name }), () => block.mutate(), false)}
            >
              {t("common.block")}
            </Button>
          )}
          <Button
            variant="light"
            color="red"
            onClick={() => confirmAction(t("admin.delete_user", { name: u.display_name }), () => remove.mutate())}
          >
            {t("common.delete")}
          </Button>
        </Group>
      </Group>
      <ErrorAlert error={block.error || unblock.error || remove.error || reissue.error} />

      <SimpleGrid cols={{ base: 1, md: 2 }}>
        <Card withBorder>
          <form onSubmit={submit}>
            <Stack>
              <ErrorAlert error={save.error} />
              <UserFields value={form} onChange={setForm} />
              <Group justify="flex-end">
                <Button type="submit" loading={save.isPending}>
                  {t("common.save")}
                </Button>
              </Group>
            </Stack>
          </form>
        </Card>
        <Card withBorder>
          <Stack>
            <Group gap="xl">
              <div>
                <Text size="xs" c="dimmed">
                  {t("traffic.down")}
                </Text>
                <Text fw={600}>{formatBytes(u.traffic_total.rx)}</Text>
              </div>
              <div>
                <Text size="xs" c="dimmed">
                  {t("traffic.up")}
                </Text>
                <Text fw={600}>{formatBytes(u.traffic_total.tx)}</Text>
              </div>
            </Group>
            <div>
              <Text size="sm" fw={500}>
                {t("traffic.by_server")}
              </Text>
              {u.traffic_by_server.map((s) => (
                <Text key={s.server_id} size="sm">
                  {s.server_name}: ↓ {formatBytes(s.rx)} · ↑ {formatBytes(s.tx)}
                </Text>
              ))}
            </div>
            <Text size="sm" fw={500}>
              {t("traffic.by_day")}
            </Text>
            {traffic.data && <TrafficChart report={traffic.data} />}
          </Stack>
        </Card>
      </SimpleGrid>

      <Group justify="space-between">
        <Title order={4}>{t("admin.user_configs")}</Title>
        <Button variant="light" onClick={() => setIssuing(true)}>
          {t("admin.add_config")}
        </Button>
      </Group>
      <ErrorAlert error={cfgBlock.error || cfgUnblock.error || cfgDelete.error} />
      <ConfigsTable
        configs={u.configs}
        onShow={(c) => void share.open(c.id)}
        onBlock={(c) => confirmAction(t("dashboard.block_config", { name: c.name }), () => cfgBlock.mutate(c), false)}
        onUnblock={(c) => cfgUnblock.mutate(c)}
        onDelete={(c) => confirmAction(t("dashboard.delete_config", { name: c.name }), () => cfgDelete.mutate(c))}
        canUnblock={() => true}
        showOwner
      />

      <Modal opened={issuing} onClose={() => setIssuing(false)} title={t("admin.add_config")}>
        <Stack>
          <ErrorAlert error={issue.error} />
          {servers.data?.flatMap((s) =>
            s.containers.map((c) => (
              <Button
                key={`${s.id}-${c.container}`}
                variant="light"
                loading={issue.isPending && issue.variables?.server_id === s.id}
                onClick={() => issue.mutate({ server_id: s.id, container: c.container })}
              >
                {`${s.name} · ${c.title}`}
              </Button>
            )),
          )}
        </Stack>
      </Modal>
      <InviteKeyModal inviteKey={inviteKey} onClose={() => setInviteKey(null)} />
      {share.modal}
    </Stack>
  );
}
