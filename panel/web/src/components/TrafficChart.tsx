import { Text } from "@mantine/core";
import { useTranslation } from "react-i18next";
import { Bar, BarChart, CartesianGrid, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import type { TrafficReport } from "../api/types";
import { formatBytes } from "../lib/format";

/** Daily traffic summed over servers. */
export function TrafficChart({ report }: { report: TrafficReport }) {
  const { t } = useTranslation();
  const days = new Map<string, { day: string; rx: number; tx: number }>();
  for (const row of report.rows) {
    const d = days.get(row.day) ?? { day: row.day, rx: 0, tx: 0 };
    d.rx += row.rx;
    d.tx += row.tx;
    days.set(row.day, d);
  }
  const data = [...days.values()];
  if (data.length === 0) return <Text c="dimmed">{t("common.none")}</Text>;
  return (
    <ResponsiveContainer width="100%" height={240}>
      <BarChart data={data}>
        <CartesianGrid strokeDasharray="3 3" vertical={false} />
        <XAxis dataKey="day" tickFormatter={(d: string) => d.slice(5)} />
        <YAxis tickFormatter={(v: number) => formatBytes(v)} width={80} />
        <Tooltip formatter={(v) => formatBytes(Number(v))} />
        <Legend />
        <Bar dataKey="rx" name={t("traffic.down")} fill="var(--mantine-color-blue-6)" />
        <Bar dataKey="tx" name={t("traffic.up")} fill="var(--mantine-color-teal-6)" />
      </BarChart>
    </ResponsiveContainer>
  );
}
