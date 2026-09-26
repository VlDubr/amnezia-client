import { Alert, Badge, Button, Card, Group, SimpleGrid, Stack, Text, Title } from "@mantine/core";
import { useTranslation } from "react-i18next";
import { api } from "../../api/client";
import { keys, useAction, useMe, useMyConfigs, useMyServers } from "../../api/hooks";
import type { Config } from "../../api/types";
import { ConfigsTable } from "../../components/ConfigsTable";
import { confirmAction } from "../../components/confirm";
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

  const create = useAction(
    (v: { server_id: number; container: string }) => api<Config>("/api/me/configs", { method: "POST", body: v }),
    refresh,
    (created) => void share.open(created.id),
  );
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
      <ErrorAlert error={create.error} />
      {servers.data && servers.data.length === 0 && <Text c="dimmed">{t("dashboard.no_servers")}</Text>}
      <SimpleGrid cols={{ base: 1, sm: 2, lg: 3 }}>
        {servers.data?.map((s) => (
          <Card key={s.id} withBorder padding="md">
            <Text fw={600} mb="xs">
              {s.name}
            </Text>
            <Stack gap="xs">
              {s.containers.map((c) => (
                <Group key={c.container} justify="space-between" wrap="nowrap">
                  <Text size="sm">{c.title}</Text>
                  <Button
                    size="xs"
                    onClick={() => create.mutate({ server_id: s.id, container: c.container })}
                    loading={create.isPending && create.variables?.server_id === s.id}
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
      {share.modal}
    </Stack>
  );
}
