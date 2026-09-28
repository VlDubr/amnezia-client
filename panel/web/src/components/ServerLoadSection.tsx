import { Alert, Button, Card, Group, NumberInput, SegmentedControl, SimpleGrid, Stack, Text, TextInput, Title } from "@mantine/core";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { api } from "../api/client";
import { keys, useAction, useServerLoad } from "../api/hooks";
import type { LoadPoint, ServerLoad } from "../api/types";
import { formatBytes, formatDateTime } from "../lib/format";
import { ErrorAlert } from "./ErrorAlert";
import { LoadBadge } from "./LoadBadge";

type Line_ = { key: keyof LoadPoint; name: string; color: string };

function Chart({ title, data, lines, unit, range }: { title: string; data: LoadPoint[]; lines: Line_[]; unit: string; range: string }) {
  const time = (ts: string) => {
    const d = new Date(ts);
    return range === "24h" ? d.toTimeString().slice(0, 5) : `${d.getDate()}.${d.getMonth() + 1}`;
  };
  return (
    <Card withBorder padding="sm">
      <Text size="sm" fw={500} mb={4}>
        {title}
      </Text>
      <ResponsiveContainer width="100%" height={160}>
        <LineChart data={data}>
          <CartesianGrid strokeDasharray="3 3" vertical={false} />
          <XAxis dataKey="ts" tickFormatter={time} minTickGap={40} />
          <YAxis width={48} unit={unit} />
          <Tooltip labelFormatter={(ts) => new Date(String(ts)).toLocaleString()} />
          {lines.length > 1 && <Legend />}
          {lines.map((l) => (
            // No connectNulls: a sample gap stays a visible gap.
            <Line key={l.key} dataKey={l.key} name={l.name} stroke={l.color} dot={false} isAnimationActive={false} connectNulls={false} />
          ))}
        </LineChart>
      </ResponsiveContainer>
    </Card>
  );
}

function pct(v: number | null | undefined) {
  return v == null ? "—" : `${Math.round(v)}%`;
}

function mbps(v: number | null | undefined) {
  return v == null ? "—" : `${Math.round(v)}`;
}

function Capacity({ serverId, load }: { serverId: number; load: ServerLoad }) {
  const { t } = useTranslation();
  const [width, setWidth] = useState<number | string>(load.capacity.bandwidth_mbps ?? "");
  const [clients, setClients] = useState<number | string>(load.capacity.expected_clients ?? "");
  const [iface, setIface] = useState(load.capacity.metrics_iface ?? "");
  useEffect(() => {
    setWidth(load.capacity.bandwidth_mbps ?? "");
    setClients(load.capacity.expected_clients ?? "");
    setIface(load.capacity.metrics_iface ?? "");
  }, [load.capacity.bandwidth_mbps, load.capacity.expected_clients, load.capacity.metrics_iface]);
  const save = useAction(
    () => api(`/api/admin/servers/${serverId}`, {
      method: "PATCH",
      // An empty field clears the value on the server.
      body: {
        bandwidth_mbps: typeof width === "number" ? width : null,
        expected_clients: typeof clients === "number" ? clients : null,
        metrics_iface: iface.trim() || null,
      },
    }),
    [keys.server(serverId), ["admin", "server", serverId, "load"], keys.servers],
  );
  return (
    <Card withBorder>
      <Stack gap="xs">
        <Title order={5}>{t("load.capacity")}</Title>
        <ErrorAlert error={save.error} />
        <NumberInput label={t("load.bandwidth")} value={width} onChange={setWidth} min={1} max={1_000_000} allowDecimal={false}
          description={[load.hints.link_mbps ? t("load.hint_link", { v: load.hints.link_mbps }) : "",
                        load.hints.peak_mbps_7d != null ? t("load.hint_peak", { v: Math.round(load.hints.peak_mbps_7d) }) : ""]
            .filter(Boolean).join(" · ")} />
        <NumberInput label={t("load.expected")} value={clients} onChange={setClients} min={1} max={100_000} allowDecimal={false} />
        <TextInput label={t("load.iface")} value={iface} onChange={(e) => setIface(e.currentTarget.value)}
          placeholder={load.specs.iface ?? ""} maxLength={15} />
        <Group>
          <Button onClick={() => save.mutate()} loading={save.isPending}>
            {t("load.save_capacity")}
          </Button>
        </Group>
      </Stack>
    </Card>
  );
}

/** Admin server page: hardware, current load, capacities, charts, peaks and recommendations (load spec §7). */
export function ServerLoadSection({ serverId }: { serverId: number }) {
  const { t, i18n } = useTranslation();
  const [range, setRange] = useState<"24h" | "7d">("24h");
  const load = useServerLoad(serverId, range);
  if (!load.data) return <ErrorAlert error={load.error} />;
  const l = load.data;
  const sp = l.specs;
  const c = l.current;
  const days = sp.uptime_s ? Math.floor(sp.uptime_s / 86400) : null;
  return (
    <Stack>
      <Group justify="space-between">
        <Group gap="xs">
          <Title order={4}>{t("load.title")}</Title>
          <LoadBadge level={l.level} size="md" />
        </Group>
        <SegmentedControl value={range} onChange={(v) => setRange(v as "24h" | "7d")}
          data={[{ value: "24h", label: t("load.range_24h") }, { value: "7d", label: t("load.range_7d") }]} />
      </Group>
      {l.metrics_error && (
        <Alert color="orange">{t("load.metrics_error", { error: l.metrics_error, at: formatDateTime(l.metrics_error_at, i18n.language) })}</Alert>
      )}
      <SimpleGrid cols={{ base: 1, md: 2 }}>
        <Card withBorder>
          <Stack gap={4}>
            <Title order={5}>{t("load.hardware")}</Title>
            <Text size="sm">{`${t("load.cpu_model")}: ${sp.cpu_model ?? "—"}${sp.cores ? ` · ${t("load.cores", { n: sp.cores })}` : ""}`}</Text>
            <Text size="sm">{`${t("load.memory")}: ${sp.mem_bytes ? formatBytes(sp.mem_bytes) : "—"} · ${t("load.disk")}: ${sp.disk_bytes ? formatBytes(sp.disk_bytes) : "—"}`}</Text>
            <Text size="sm">{`${t("load.os")}: ${sp.os ?? "—"} · ${sp.kernel ?? ""}`}</Text>
            <Text size="sm">{`${t("load.iface_short")}: ${sp.iface ?? "—"}${sp.link_mbps ? ` (${sp.link_mbps} ${t("load.mbps")})` : ""}`}</Text>
            {days != null && <Text size="sm">{t("load.uptime", { days })}</Text>}
          </Stack>
        </Card>
        <Card withBorder>
          <Stack gap={4}>
            <Title order={5}>{t("load.current")}</Title>
            <Text size="sm">{`${t("load.cpu")}: ${pct(c?.cpu)} · ${t("load.mem")}: ${pct(c?.mem)} · ${t("load.disk")}: ${pct(c?.disk)}`}</Text>
            <Text size="sm">{`${t("load.load1")}: ${c?.load1 ?? "—"}`}</Text>
            <Text size="sm">{`${t("load.channel")}: ↓ ${mbps(c?.rx)} / ↑ ${mbps(c?.tx)} ${t("load.mbps")}`}</Text>
            <Text size="sm">{`${t("load.clients")}: ${c?.clients ?? "—"}`}</Text>
            {l.untracked_protocols.length > 0 && (
              <Text size="xs" c="dimmed">{t("load.untracked_note", { protocols: l.untracked_protocols.join(", ") })}</Text>
            )}
          </Stack>
        </Card>
      </SimpleGrid>

      <Card withBorder>
        <Stack gap="xs">
          <Title order={5}>{t("load.recommendations")}</Title>
          {l.recommendations.length === 0 && <Text size="sm" c="dimmed">{t("load.no_recommendations")}</Text>}
          {l.recommendations.map((r) => (
            <Alert key={r.code} color={r.severity === "warning" ? "orange" : "blue"} variant="light">
              {t(`load.rec.${r.code}`, {
                ...r.params,
                protocols: Array.isArray(r.params.protocols) ? (r.params.protocols as string[]).join(", ") : undefined,
                error: r.params.error ?? t("load.unknown_error"),
              })}
            </Alert>
          ))}
        </Stack>
      </Card>

      <Capacity serverId={serverId} load={l} />

      <SimpleGrid cols={{ base: 1, md: 2 }}>
        <Chart title={t("load.cpu")} data={l.series} range={range} unit="%" lines={[{ key: "cpu", name: t("load.cpu"), color: "var(--mantine-color-blue-6)" }]} />
        <Chart title={t("load.mem")} data={l.series} range={range} unit="%" lines={[{ key: "mem", name: t("load.mem"), color: "var(--mantine-color-grape-6)" }]} />
        <Chart title={`${t("load.channel")}, ${t("load.mbps")}`} data={l.series} range={range} unit=""
          lines={[{ key: "rx", name: t("load.in"), color: "var(--mantine-color-teal-6)" }, { key: "tx", name: t("load.out"), color: "var(--mantine-color-orange-6)" }]} />
        <Chart title={t("load.clients")} data={l.series} range={range} unit="" lines={[{ key: "clients", name: t("load.clients"), color: "var(--mantine-color-cyan-6)" }]} />
      </SimpleGrid>
      <Text size="sm" c="dimmed">
        {`${t("load.peaks")}: ${t("load.cpu")} ${pct(l.peaks.cpu)} · ${t("load.mem")} ${pct(l.peaks.mem)} · ${t("load.channel")} ${mbps(l.peaks.net)} ${t("load.mbps")} · ${t("load.clients")} ${l.peaks.clients ?? "—"}`}
      </Text>
    </Stack>
  );
}
