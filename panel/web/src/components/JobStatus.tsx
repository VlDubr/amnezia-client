import { Alert, Loader, Group, Text } from "@mantine/core";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect } from "react";
import { useTranslation } from "react-i18next";
import { fetchJob } from "../api/hooks";

/** Follows a background server job until it finishes, then refreshes admin data. */
export function JobStatus({ jobId, pollMs = 1000 }: { jobId: number | null; pollMs?: number }) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const job = useQuery({
    queryKey: ["job", jobId],
    queryFn: () => fetchJob(jobId!),
    enabled: jobId !== null,
    refetchInterval: (q) => (q.state.data && ["done", "failed"].includes(q.state.data.status) ? false : pollMs),
  });
  const status = job.data?.status;
  useEffect(() => {
    if (status === "done" || status === "failed") void queryClient.invalidateQueries({ queryKey: ["admin"] });
  }, [status, queryClient]);

  if (jobId === null || !job.data) return null;
  if (status === "done") return <Alert color="teal">{t("admin.job_done")}</Alert>;
  if (status === "failed") return <Alert color="red">{t("admin.job_failed", { error: job.data.last_error ?? "" })}</Alert>;
  return (
    <Group gap="xs">
      <Loader size="xs" />
      <Text size="sm">
        {t("admin.job_running")}
        {job.data.last_error ? ` (${job.data.last_error})` : ""}
      </Text>
    </Group>
  );
}
