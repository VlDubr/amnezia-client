import { Badge } from "@mantine/core";
import { useTranslation } from "react-i18next";
import type { LoadLevel } from "../api/types";

const COLORS: Record<LoadLevel, string> = { low: "teal", medium: "yellow", high: "red", unknown: "gray" };

/** The server load level: the only load information users get. */
export function LoadBadge({ level, size = "sm" }: { level: LoadLevel; size?: "xs" | "sm" | "md" }) {
  const { t } = useTranslation();
  return (
    <Badge color={COLORS[level]} variant="light" size={size}>
      {t(`load.level.${level}`)}
    </Badge>
  );
}
