import { Badge } from "@mantine/core";
import { useTranslation } from "react-i18next";

const COLORS: Record<string, string> = {
  active: "teal",
  blocked: "red",
  expired: "orange",
  inactive: "gray",
  deleting: "gray",
};

export function StatusBadge({ status, blockedBy }: { status: string; blockedBy?: string | null }) {
  const { t } = useTranslation();
  const byWhom = status === "blocked" && (blockedBy === "user" || blockedBy === "admin") ? blockedBy : null;
  const label = byWhom ? t(`status.blocked_by_${byWhom}`) : t(`status.${status}`);
  return (
    <Badge color={COLORS[status] ?? "gray"} variant="light">
      {label}
    </Badge>
  );
}
