import {
  Alert, Anchor, Badge, Button, Card, Code, Group, Modal, NumberInput, PasswordInput, Stack, Switch, Text, Textarea,
  TextInput, Title,
} from "@mantine/core";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Link, useNavigate, useParams } from "react-router";
import { ApiError, api } from "../../api/client";
import { keys, useAction, useInstallable, useServer } from "../../api/hooks";
import { confirmAction } from "../../components/confirm";
import { ServerLoadSection } from "../../components/ServerLoadSection";
import { ErrorAlert } from "../../components/ErrorAlert";
import { JobStatus } from "../../components/JobStatus";
import { XrayOptionsSection } from "../../components/XrayOptionsSection";
import { formatDateTime } from "../../lib/format";

type Job = { job_id: number };
type JumpDraft = { host: string; port: number; user: string; password: string; key: string };

export default function ServerPage() {
  const { t, i18n } = useTranslation();
  const id = Number(useParams().id);
  const navigate = useNavigate();
  const server = useServer(id);
  const installable = useInstallable();
  const [jobId, setJobId] = useState<number | null>(null);
  const [installing, setInstalling] = useState(false);
  const [port, setPort] = useState("");
  const [relayEditing, setRelayEditing] = useState(false);
  const [relayHost, setRelayHost] = useState("");
  const [jumpEditing, setJumpEditing] = useState(false);
  const [jumpDraft, setJumpDraft] = useState<JumpDraft>({ host: "", port: 22, user: "root", password: "", key: "" });
  const refresh = [keys.server(id), keys.servers];
  const onJob = (r: Job) => setJobId(r.job_id);

  const sync = useAction(() => api<Job>(`/api/admin/servers/${id}/sync`, { method: "POST" }), refresh, onJob);
  const accept = useAction(() => api<Job>(`/api/admin/servers/${id}/host-key/accept`, { method: "POST" }), refresh, onJob);
  const toggle = useAction(
    (enabled: boolean) => api(`/api/admin/servers/${id}`, { method: "PATCH", body: { enabled_for_users: enabled } }),
    refresh,
  );
  const patchRelay = useAction(
    () => api(`/api/admin/servers/${id}`, { method: "PATCH", body: { client_host: relayHost.trim() || null } }),
    refresh,
    () => setRelayEditing(false),
  );
  const patchJump = useAction(
    () => api(`/api/admin/servers/${id}`, {
      method: "PATCH",
      body: {
        ssh_jump: jumpDraft.host.trim()
          ? {
              host: jumpDraft.host.trim(), port: jumpDraft.port, user: jumpDraft.user.trim(),
              password: jumpDraft.password || null, private_key: jumpDraft.key || null,
            }
          : null,
      },
    }),
    refresh,
    () => setJumpEditing(false),
  );
  const removeJump = useAction(
    () => api(`/api/admin/servers/${id}`, { method: "PATCH", body: { ssh_jump: null } }),
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
      <ErrorAlert error={sync.error || accept.error || toggle.error || remove.error || patchRelay.error
        || patchJump.error || removeJump.error} />
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
          {s.client_host && (
            <Text size="sm">{`${t("admin.client_host")}: ${s.client_host}`}</Text>
          )}
          {s.ssh_jump && (
            <Text size="sm">{`${t("admin.ssh_jump")}: ${s.ssh_jump.host}:${s.ssh_jump.port} (${s.ssh_jump.user})`}</Text>
          )}
          <Group gap="xs">
            <Button size="xs" variant="subtle"
              onClick={() => { setRelayHost(s.client_host ?? ""); setRelayEditing(true); }}>
              {t("admin.client_host")}
            </Button>
            <Button size="xs" variant="subtle"
              onClick={() => {
                setJumpDraft({ host: "", port: 22, user: "root", password: "", key: "" });
                setJumpEditing(true);
              }}>
              {t("admin.ssh_jump")}
            </Button>
            {s.ssh_jump && (
              <Button size="xs" variant="subtle" color="red" loading={removeJump.isPending}
                onClick={() => confirmAction(t("admin.ssh_jump_remove"), () => removeJump.mutate())}>
                {t("admin.ssh_jump_remove")}
              </Button>
            )}
          </Group>
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

      {s.containers.some((c) => c.container === "amnezia-xray") && <XrayOptionsSection serverId={id} />}

      <ServerLoadSection serverId={id} />

      <Modal opened={relayEditing} onClose={() => setRelayEditing(false)} title={t("admin.client_host")}>
        <Stack>
          <ErrorAlert error={patchRelay.error} />
          <TextInput label={t("admin.client_host")} description={t("admin.client_host_hint")}
            value={relayHost} onChange={(e) => setRelayHost(e.currentTarget.value.trim())} autoFocus />
          <Group justify="flex-end">
            <Button onClick={() => patchRelay.mutate()} loading={patchRelay.isPending}>{t("common.save")}</Button>
          </Group>
        </Stack>
      </Modal>

      <Modal opened={jumpEditing} onClose={() => setJumpEditing(false)} title={t("admin.ssh_jump")}>
        <Stack>
          <ErrorAlert error={patchJump.error} />
          <TextInput label={t("admin.ssh_jump_host")} value={jumpDraft.host}
            description={s.ssh_jump ? `${s.ssh_jump.host}:${s.ssh_jump.port} (${s.ssh_jump.user})` : undefined}
            onChange={(e) => setJumpDraft({ ...jumpDraft, host: e.currentTarget.value.trim() })} autoFocus />
          <Group grow>
            <NumberInput label={t("admin.ssh_jump_port")} value={jumpDraft.port} min={1} max={65535}
              allowDecimal={false} onChange={(v) => setJumpDraft({ ...jumpDraft, port: Number(v) || 22 })} />
            <TextInput label={t("admin.ssh_jump_user")} value={jumpDraft.user}
              onChange={(e) => setJumpDraft({ ...jumpDraft, user: e.currentTarget.value.trim() })} />
          </Group>
          <PasswordInput label={t("admin.ssh_jump_password")} value={jumpDraft.password} autoComplete="off"
            onChange={(e) => setJumpDraft({ ...jumpDraft, password: e.currentTarget.value })} />
          <Textarea label={t("admin.ssh_jump_key")} value={jumpDraft.key} autosize minRows={2} maxRows={6}
            styles={{ input: { fontFamily: "monospace", fontSize: 12 } }}
            onChange={(e) => setJumpDraft({ ...jumpDraft, key: e.currentTarget.value })} />
          <Group justify="flex-end">
            <Button onClick={() => patchJump.mutate()} loading={patchJump.isPending}
            >
              {t("common.save")}
            </Button>
          </Group>
        </Stack>
      </Modal>

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
