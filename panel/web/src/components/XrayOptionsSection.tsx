import { Button, Card, Group, Input, SegmentedControl, Select, Stack, Text, TextInput } from "@mantine/core";
import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { api } from "../api/client";
import { keys, useAction, useRenderOptions } from "../api/hooks";
import type { XrayRenderOptionsPatch } from "../api/types";
import { ErrorAlert } from "./ErrorAlert";

const XRAY = "amnezia-xray";
const DEFAULTS = { fp: "firefox", length: "100-200", interval: "10-20", mihomo: "chrome", spider: "" };
const MIHOMO_FINGERPRINTS = ["chrome", "firefox", "safari", "ios", "android", "edge", "random"];
// ASCII digits only, as the backend requires: Xray cannot parse Unicode digits.
const RANGE = /^[0-9]{1,5}-[0-9]{1,5}$/;

type Form = { fp: string; length: string; interval: string; mihomo: string; spider: string };

function rangeOk(value: string, minFrom: number): boolean {
  if (!RANGE.test(value)) return false;
  const [from, to] = value.split("-").map(Number);
  return from >= minFrom && from <= to && to <= 65535;
}

// Same rules as the backend: a path from /, at most 256 code points, no Unicode control characters.
function spiderOk(value: string): boolean {
  return value === "" || (value.startsWith("/") && Array.from(value).length <= 256 && !/\p{Cc}/u.test(value));
}

/** Admin overrides for the Xray client profiles of one server container. The server itself is not touched;
 * the options apply to configs rendered afterwards. Save sends only the fields changed since the values were
 * loaded (a changed field back at its default is sent as null), so concurrent admins do not overwrite each
 * other's untouched overrides. */
export function XrayOptionsSection({ serverId }: { serverId: number }) {
  const { t } = useTranslation();
  const query = useRenderOptions(serverId, XRAY);
  const [base, setBase] = useState<Form>(DEFAULTS);
  const [form, setForm] = useState<Form>(DEFAULTS);

  const effective = query.data?.effective;
  const dirty = form.fp !== base.fp || form.length !== base.length || form.interval !== base.interval
    || form.mihomo !== base.mihomo || form.spider !== base.spider;
  // Hydrate by fetch time, not object identity: react-query keeps the object when the data is deeply equal,
  // so a revert by another admin arriving with unchanged values must still refresh a pristine form.
  const hydratedAt = useRef(0);
  useEffect(() => {
    // A refetch must not discard unsaved edits, and the pre-save cache must not undo our own save.
    if (!effective || dirty || hydratedAt.current === query.dataUpdatedAt) return;
    hydratedAt.current = query.dataUpdatedAt;
    const loaded: Form = { fp: effective.mobile_fingerprint, length: effective.fragment.length,
                           interval: effective.fragment.interval, mihomo: effective.mihomo_fingerprint,
                           spider: effective.spider_x };
    setBase(loaded);
    setForm(loaded);
  }, [effective, dirty, query.dataUpdatedAt]);

  const save = useAction(
    (body: XrayRenderOptionsPatch) =>
      api(`/api/admin/servers/${serverId}/containers/${XRAY}/render-options`, { method: "PATCH", body }),
    [keys.renderOptions(serverId, XRAY)],
    (_result, vars) => {
      // The saved values become the new baseline for the next diff; a full reset lands on the defaults.
      const fullReset = vars.mobile_fingerprint === null && vars.mihomo_fingerprint === null
        && vars.spider_x === null && vars.fragment?.length === null && vars.fragment?.interval === null;
      setBase(fullReset ? DEFAULTS : form);
      if (fullReset) setForm(DEFAULTS);
    },
  );

  function changedBody(): XrayRenderOptionsPatch {
    const body: XrayRenderOptionsPatch = {};
    if (form.fp !== base.fp) body.mobile_fingerprint = form.fp === DEFAULTS.fp ? null : (form.fp as "firefox" | "ios");
    const fragment: { length?: string | null; interval?: string | null } = {};
    if (form.length !== base.length) fragment.length = form.length === DEFAULTS.length ? null : form.length;
    if (form.interval !== base.interval) fragment.interval = form.interval === DEFAULTS.interval ? null : form.interval;
    if (fragment.length !== undefined || fragment.interval !== undefined) body.fragment = fragment;
    if (form.mihomo !== base.mihomo) body.mihomo_fingerprint = form.mihomo === DEFAULTS.mihomo ? null : form.mihomo;
    if (form.spider !== base.spider) body.spider_x = form.spider === DEFAULTS.spider ? null : form.spider;
    return body;
  }

  const resetBody: XrayRenderOptionsPatch = {
    mobile_fingerprint: null, fragment: { length: null, interval: null }, mihomo_fingerprint: null, spider_x: null,
  };

  if (query.error) return <ErrorAlert error={query.error} />;
  if (!query.data) return null;

  const lengthOk = rangeOk(form.length, 1);
  const intervalOk = rangeOk(form.interval, 0);
  const pathOk = spiderOk(form.spider);
  const valid = lengthOk && intervalOk && pathOk;
  const set = (patch: Partial<Form>) => setForm((f) => ({ ...f, ...patch }));

  return (
    <Card withBorder>
      <Stack gap="xs">
        <Text size="sm" fw={500}>
          {t("admin.xray_options")}
        </Text>
        <Text size="xs" c="dimmed">
          {t("admin.xray_options_hint")}
        </Text>
        <ErrorAlert error={save.error} />
        <Group grow align="flex-end">
          <Input.Wrapper label={t("admin.xray_mobile_fp")} size="sm">
            <SegmentedControl value={form.fp} onChange={(fp) => set({ fp })} data={["firefox", "ios"]}
              disabled={save.isPending} aria-label={t("admin.xray_mobile_fp")} />
          </Input.Wrapper>
          <Select label={t("admin.xray_mihomo_fp")} data={MIHOMO_FINGERPRINTS} value={form.mihomo} allowDeselect={false}
            onChange={(v) => set({ mihomo: v ?? DEFAULTS.mihomo })} disabled={save.isPending} />
        </Group>
        <Group grow>
          <TextInput label={t("admin.xray_frag_length")} value={form.length}
            onChange={(e) => set({ length: e.currentTarget.value })}
            error={lengthOk ? undefined : t("admin.xray_range_error")} disabled={save.isPending} />
          <TextInput label={t("admin.xray_frag_interval")} value={form.interval}
            onChange={(e) => set({ interval: e.currentTarget.value })}
            error={intervalOk ? undefined : t("admin.xray_range_error")} disabled={save.isPending} />
        </Group>
        <TextInput label={t("admin.xray_spider")} value={form.spider} onChange={(e) => set({ spider: e.currentTarget.value })}
          error={pathOk ? undefined : t("admin.xray_spider_error")} disabled={save.isPending} />
        <Group>
          <Button onClick={() => save.mutate(changedBody())} loading={save.isPending} disabled={!valid || !dirty}>
            {t("common.save")}
          </Button>
          <Button variant="default" onClick={() => save.mutate(resetBody)} loading={save.isPending}
            disabled={save.isPending}>
            {t("admin.xray_reset")}
          </Button>
        </Group>
        {save.isSuccess && !dirty && (
          <Text size="xs" c="dimmed">
            {t("admin.xray_saved_hint")}
          </Text>
        )}
      </Stack>
    </Card>
  );
}
