import { Button, Paper, PasswordInput, Stack, Text, Title } from "@mantine/core";
import { useMutation } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { api } from "../../api/client";
import { useMe } from "../../api/hooks";
import { ErrorAlert } from "../../components/ErrorAlert";
import { PasswordField, passwordProblem } from "../../components/PasswordField";

/** Б.4: the user sets a new password; other sessions are signed out by the server. */
export default function AccountPage() {
  const { t } = useTranslation();
  const me = useMe();
  const [old, setOld] = useState("");
  const [next, setNext] = useState("");
  const [repeat, setRepeat] = useState("");
  const [mismatch, setMismatch] = useState(false);
  const [done, setDone] = useState(false);
  const userInputs = [me.data?.login ?? "", me.data?.display_name ?? ""];

  const change = useMutation({
    mutationFn: () => api("/api/me/password", { method: "POST", body: { old, new: next } }),
    onSuccess: () => {
      setOld("");
      setNext("");
      setRepeat("");
      setDone(true);
    },
  });

  function submit(e: FormEvent) {
    e.preventDefault();
    setDone(false);
    if (next !== repeat) {
      setMismatch(true);
      return;
    }
    setMismatch(false);
    change.mutate();
  }

  return (
    <Stack maw={480}>
      <Title order={2}>{t("account.title")}</Title>
      {me.data && (
        <Text c="dimmed">
          {me.data.display_name} · {me.data.login}
        </Text>
      )}
      <Paper withBorder p="lg">
        <form onSubmit={submit}>
          <Stack>
            <Title order={4}>{t("account.change_password")}</Title>
            <ErrorAlert error={change.error} />
            {done && <Text c="teal">{t("account.changed")}</Text>}
            <PasswordInput
              label={t("account.old_password")}
              value={old}
              onChange={(e) => setOld(e.currentTarget.value)}
              autoComplete="current-password"
              required
            />
            <PasswordField label={t("account.new_password")} value={next} onChange={setNext} userInputs={userInputs} />
            <PasswordInput
              label={t("auth.password_repeat")}
              value={repeat}
              onChange={(e) => setRepeat(e.currentTarget.value)}
              error={mismatch ? t("auth.passwords_differ") : undefined}
              autoComplete="new-password"
              required
            />
            <Button type="submit" loading={change.isPending} disabled={!!passwordProblem(next, userInputs)}>
              {t("common.save")}
            </Button>
          </Stack>
        </form>
      </Paper>
    </Stack>
  );
}
