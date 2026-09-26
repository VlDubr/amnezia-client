import { Alert, Anchor, Badge, Button, Card, Code, Group, Modal, Stack, Switch, Text, TextInput, Title } from "@mantine/core";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Link, useNavigate, useParams } from "react-router";
import { ApiError, api } from "../../api/client";
import { keys, useAction, useInstallable, useServer } from "../../api/hooks";
import { confirmAction } from "../../components/confirm";
import { ErrorAlert } from "../../components/ErrorAlert";
import { JobStatus } from "../../components/JobStatus";
import { formatDateTime } from "../../lib/format";

type Job = { job_id: number };

export default function ServerPage() {
  const { t, i18n } = useTranslation();
  const id = Number(useParams().id);
  const navigate = useNavigate();
  const server = useServer(id);
  const installable = useInstallable();
  const [jobId, setJobId] = useState<number | null>(null);
  const [installing, setInstalling] = useState(false);
  const [port, setPort] = useState("");
  const refresh = [keys.server(id), keys.servers];
  const onJob = (r: Job) => setJobId(r.job_id);

  const sync = useAction(() => api<Job>(`/api/admin/servers/${id}/sync`, { method: "POST" }), refresh, onJob);
  const accept = useAction(() => api<Job>(`/api/admin/servers/${id}/host-key/accept`, { method: "POST" }), refresh, onJob);
  const toggle = useAction(
    (enabled: boolean) => api(`/api/admin/servers/${id}`, { method: "PATCH", body: { enabled_for_users: enabled } }),
    refresh,
  );
  const remove = useAction(() => api(`/api/admin/servers/${id}`, { method: "DELETE" }), [keys.servers], () =>
    navigate("/admin/servers"),
  );
  const install = useAction(
    (v: { container: string; force: boolean }) =>
      api<Job>(`/api/admin/servers/${id}/containers`, {
        method: "POST",
        body: { container: v.container, ...(port ? { port } : {}), ...(v.force ? { force: true } : {}) },
      }),
    refresh,
    (r) => {
      setInstalling(false);
      onJob(r);
    },
  );

  function startInstall(container: string) {
    install.mutate(
      { container, force: false },
      {
        onError: (e) => {
          if (e instanceof ApiError && e.code === "already_installed") {
            install.reset();
            confirmAction(t("admin.reinstall_confirm"), () => install.mutate({ container, force: true }));
          }
        },
      },
    );
  }

  const s = server.data;
  if (!s) return <ErrorAlert error={server.error} />;
  const hostKeyChanged = !!s.last_error?.includes("host_key_mismatch");

  return (
    <Stack>
      <Anchor component={Link} to="/admin/servers" size="sm">
        ← {t("admin.servers")}
      </Anchor>
      <Group justify="space-between">
        <Title order={2}>{s.name}</Title>
        <Group gap="xs">
          <Button variant="light" onClick={() => sync.mutate()} loading={sync.isPending}>
            {t("admin.sync")}
          </Button>
          <Button variant="light" onClick={() => setInstalling(true)}>
            {t("admin.install")}
          </Button>
          <Button variant="light" color="red"
            onClick={() => confirmAction(t("admin.delete_server", { name: s.name }), () => remove.mutate())}>
            {t("common.delete")}
          </Button>
        </Group>
      </Group>
      <JobStatus jobId={jobId} />
      <ErrorAlert error={sync.error || accept.error || toggle.error || remove.error} />
      {hostKeyChanged && (
        <Alert color="red">
          <Stack gap="xs">
            <Text size="sm">{t("admin.host_key_changed")}</Text>
            <Group>
              <Button size="xs" color="red"
                onClick={() => confirmAction(t("admin.host_key_changed"), () => accept.mutate())}>
                {t("admin.accept_host_key")}
              </Button>
            </Group>
          </Stack>
        </Alert>
      )}
      {s.last_error && !hostKeyChanged && <Alert color="orange">{`${t("admin.last_error")}: ${s.last_error}`}</Alert>}

      <Card withBorder>
        <Stack gap="xs">
          <Text size="sm">{`${t("admin.host")}: ${s.host}:${s.ssh_port} (${s.ssh_user})`}</Text>
          <Text size="sm">{`${t("admin.last_ok")}: ${formatDateTime(s.last_ok_at, i18n.language)}`}</Text>
          <Switch label={t("admin.enabled_for_users")} checked={s.enabled_for_users}
            onChange={(e) => toggle.mutate(e.currentTarget.checked)} />
          <Text size="sm" fw={500}>
            {t("admin.containers")}
          </Text>
          <Group gap={4}>
            {s.containers.map((c) => (
              <Badge key={c.container} variant="light">
                {c.title}
                {c.port ? ` :${c.port}` : ""}
              </Badge>
            ))}
          </Group>
          {s.host_key && (
            <>
              <Text size="sm" fw={500}>
                {t("admin.host_key")}
              </Text>
              <Code block style={{ whiteSpace: "pre-wrap", wordBreak: "break-all" }}>
                {s.host_key}
              </Code>
            </>
          )}
        </Stack>
      </Card>

      <Modal opened={installing} onClose={() => setInstalling(false)} title={t("admin.install")}>
        <Stack>
          <ErrorAlert error={install.error} />
          <TextInput label={t("admin.install_port")} value={port} onChange={(e) => setPort(e.currentTarget.value.replace(/\D/g, ""))} />
          {installable.data?.map((c) => (
            <Button key={c.container} variant="light" loading={install.isPending} onClick={() => startInstall(c.container)}>
              {c.title}
            </Button>
          ))}
        </Stack>
      </Modal>
    </Stack>
  );
}
