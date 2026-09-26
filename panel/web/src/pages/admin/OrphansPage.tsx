import { Alert, Button, Modal, Stack, TextInput, Title } from "@mantine/core";
import { useDebouncedValue } from "@mantine/hooks";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { api } from "../../api/client";
import { keys, useAction, useOrphans, useUsers } from "../../api/hooks";
import type { Config } from "../../api/types";
import { ConfigsTable } from "../../components/ConfigsTable";
import { confirmAction } from "../../components/confirm";
import { ErrorAlert } from "../../components/ErrorAlert";
import { useShare } from "../../components/useShare";

/** Clients imported from servers (created outside the panel): keep, block, delete or assign to a user. */
export default function OrphansPage() {
  const { t } = useTranslation();
  const orphans = useOrphans();
  const share = useShare("/api/admin/configs");
  const [assigning, setAssigning] = useState<Config | null>(null);
  const [q, setQ] = useState("");
  const [debouncedQ] = useDebouncedValue(q, 300);
  const users = useUsers(new URLSearchParams(debouncedQ ? { q: debouncedQ } : {}));
  const refresh = [keys.orphans, ["admin", "users"]];

  const assign = useAction(
    (v: { config: Config; user_id: number }) =>
      api(`/api/admin/configs/${v.config.id}/assign`, { method: "POST", body: { user_id: v.user_id } }),
    refresh,
    () => setAssigning(null),
  );
  const block = useAction((c: Config) => api(`/api/admin/configs/${c.id}/block`, { method: "POST" }), refresh);
  const unblock = useAction((c: Config) => api(`/api/admin/configs/${c.id}/unblock`, { method: "POST" }), refresh);
  const remove = useAction((c: Config) => api(`/api/admin/configs/${c.id}`, { method: "DELETE" }), refresh);

  return (
    <Stack>
      <Title order={2}>{t("admin.orphans")}</Title>
      <Alert color="blue" variant="light">
        {t("admin.orphans_hint")}
      </Alert>
      <ErrorAlert error={orphans.error || block.error || unblock.error || remove.error} />
      {orphans.data?.length === 0 && <Alert color="gray">{t("admin.no_orphans")}</Alert>}
      {!!orphans.data?.length && (
        <ConfigsTable
          configs={orphans.data}
          canUnblock={() => true}
          showOwner
          onShow={(c) => void share.open(c.id)}
          onBlock={(c) => confirmAction(t("dashboard.block_config", { name: c.name }), () => block.mutate(c), false)}
          onUnblock={(c) => unblock.mutate(c)}
          onDelete={(c) => confirmAction(t("dashboard.delete_config", { name: c.name }), () => remove.mutate(c))}
          extraActions={(c) => (
            <Button size="compact-xs" variant="light" onClick={() => setAssigning(c)}>
              {t("admin.assign")}
            </Button>
          )}
        />
      )}
      <Modal opened={assigning !== null} onClose={() => { setAssigning(null); assign.reset(); }} title={t("admin.assign")}>
        <Stack>
          <ErrorAlert error={assign.error} />
          <TextInput placeholder={t("common.search")} value={q} onChange={(e) => setQ(e.currentTarget.value)} />
          {users.data?.map((u) => (
            <Button key={u.id} variant="light" loading={assign.isPending}
              onClick={() => assign.mutate({ config: assigning!, user_id: u.id })}>
              {u.login ? `${u.display_name} (${u.login})` : u.display_name}
            </Button>
          ))}
        </Stack>
      </Modal>
      {share.modal}
    </Stack>
  );
}
