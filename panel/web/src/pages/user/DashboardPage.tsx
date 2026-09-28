import { Alert, Badge, Button, Card, Group, Modal, SimpleGrid, Stack, Text, TextInput, Title } from "@mantine/core";
import { useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { api } from "../../api/client";
import { keys, useAction, useMe, useMyConfigs, useMyServers } from "../../api/hooks";
import type { Config } from "../../api/types";
import { ConfigsTable } from "../../components/ConfigsTable";
import { confirmAction } from "../../components/confirm";
import { LoadBadge } from "../../components/LoadBadge";
import { ErrorAlert } from "../../components/ErrorAlert";
import { useShare } from "../../components/useShare";
import { formatDate } from "../../lib/format";

/** Б.3: servers, config limit, creating, sharing, blocking and deleting own configs. */
export default function DashboardPage() {
  const { t, i18n } = useTranslation();
  const me = useMe();
  const servers = useMyServers();
  const configs = useMyConfigs();
  const share = useShare("/api/me/configs");
  const refresh = [keys.me, keys.myConfigs];

  const [naming, setNaming] = useState<{ server_id: number; container: string; name: string } | null>(null);
  const create = useAction(
    (v: { server_id: number; container: string; name: string }) =>
      api<Config>("/api/me/configs", { method: "POST", body: v }),
    refresh,
    (created) => {
      setNaming(null);
      void share.open(created.id);
    },
  );
  const startCreate = (server_id: number, container: string, name: string) => {
    create.reset();
    setNaming({ server_id, container, name });
  };
  const submitCreate = (e: FormEvent) => {
    e.preventDefault();
    if (naming && naming.name.trim()) create.mutate({ ...naming, name: naming.name.trim() });
  };
  const block = useAction((c: Config) => api(`/api/me/configs/${c.id}/block`, { method: "POST" }), refresh);
  const unblock = useAction((c: Config) => api(`/api/me/configs/${c.id}/unblock`, { method: "POST" }), refresh);
  const remove = useAction((c: Config) => api(`/api/me/configs/${c.id}`, { method: "DELETE" }), refresh);

  const profile = me.data;
  const active = profile?.status === "active";
  const atLimit = !!profile && profile.configs_count >= profile.max_configs;

  return (
    <Stack>
      <Group justify="space-between" align="flex-end">
        <Title order={2}>{t("dashboard.title")}</Title>
        {profile && (
          <Group gap="xs">
            <Badge variant="light" size="lg">
              {profile.expires_on
                ? t("dashboard.access_until", { date: formatDate(profile.expires_on, i18n.language) })
                : t("dashboard.access_unlimited")}
            </Badge>
            <Badge variant="light" size="lg" color={atLimit ? "orange" : "blue"}>
              {t("dashboard.configs_used", { used: profile.configs_count, max: profile.max_configs })}
            </Badge>
          </Group>
        )}
      </Group>

      {profile?.status === "blocked" && <Alert color="red">{t("dashboard.banner_blocked")}</Alert>}
      {profile?.status === "expired" && <Alert color="orange">{t("dashboard.banner_expired")}</Alert>}
      <ErrorAlert error={me.error || servers.error || configs.error} />

      <Title order={4}>{t("dashboard.servers")}</Title>
      {servers.data && servers.data.length === 0 && <Text c="dimmed">{t("dashboard.no_servers")}</Text>}
      <SimpleGrid cols={{ base: 1, sm: 2, lg: 3 }}>
        {servers.data?.map((s) => (
          <Card key={s.id} withBorder padding="md" data-testid="server-card">
            <Group justify="space-between" mb="xs" wrap="nowrap">
              <Text fw={600} data-testid="server-name">
                {s.name}
              </Text>
              <Group gap={4} wrap="nowrap">
                {/* A failed refresh keeps old data on screen: do not keep recommending from it. */}
                {s.recommended && !servers.isError && (
                  <Badge color="teal" size="sm">
                    {t("load.recommended")}
                  </Badge>
                )}
                <LoadBadge level={s.load} />
              </Group>
            </Group>
            <Stack gap="xs">
              {s.containers.map((c) => (
                <Group key={c.container} justify="space-between" wrap="nowrap">
                  <Text size="sm">{c.title}</Text>
                  <Button
                    size="xs"
                    onClick={() => startCreate(s.id, c.container, `${s.name} ${c.title}`)}
                    disabled={!active || atLimit}
                  >
                    {t("dashboard.create_config")}
                  </Button>
                </Group>
              ))}
            </Stack>
          </Card>
        ))}
      </SimpleGrid>
      {atLimit && active && (
        <Text size="sm" c="orange">
          {t("dashboard.limit_reached")}
        </Text>
      )}

      <ErrorAlert error={block.error || unblock.error || remove.error} />
      <ConfigsTable
        configs={configs.data ?? []}
        showDisabled={!active}
        onShow={(c) => void share.open(c.id)}
        onBlock={(c) => confirmAction(t("dashboard.block_config", { name: c.name }), () => block.mutate(c), false)}
        onUnblock={(c) => unblock.mutate(c)}
        onDelete={(c) => confirmAction(t("dashboard.delete_config", { name: c.name }), () => remove.mutate(c))}
        canUnblock={(c) => c.blocked_by === "user"}
      />
      <Modal opened={naming !== null} onClose={() => setNaming(null)} title={t("dashboard.create_config")}>
        <form onSubmit={submitCreate}>
          <Stack>
            <ErrorAlert error={create.error} />
            <TextInput
              label={t("dashboard.config_name")}
              description={t("dashboard.config_name_hint")}
              value={naming?.name ?? ""}
              onChange={(e) => {
                const name = e.currentTarget.value;
                setNaming((n) => (n ? { ...n, name } : n));
              }}
              maxLength={128}
              data-autofocus
            />
            <Button type="submit" loading={create.isPending} disabled={!naming?.name.trim()}>
              {t("common.create")}
            </Button>
          </Stack>
        </form>
      </Modal>
      {share.modal}
    </Stack>
  );
}
