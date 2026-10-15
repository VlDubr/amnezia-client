import { Anchor, Button, PasswordInput, Stack, Text, TextInput } from "@mantine/core";
import { useMutation } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router";
import { api } from "../../api/client";
import { AuthLayout } from "../../components/AuthLayout";
import { ErrorAlert } from "../../components/ErrorAlert";
import { PasswordField, passwordProblem } from "../../components/PasswordField";

/** The account owner enters the reset key from the administrator, then the new password twice. */
export default function ResetPasswordPage() {
  const { t } = useTranslation();
  const [key, setKey] = useState("");
  const [login, setLogin] = useState<string | null>(null);
  const [password, setPassword] = useState("");
  const [repeat, setRepeat] = useState("");
  const [mismatch, setMismatch] = useState(false);

  const check = useMutation({
    mutationFn: () => api<{ login: string }>("/api/auth/reset/check", { method: "POST", body: { key } }),
    onSuccess: (r) => setLogin(r.login),
  });
  const reset = useMutation({
    mutationFn: () => api("/api/auth/reset", { method: "POST", body: { key, password } }),
  });

  function submitKey(e: FormEvent) {
    e.preventDefault();
    check.mutate();
  }

  function submitPassword(e: FormEvent) {
    e.preventDefault();
    if (password !== repeat) {
      setMismatch(true);
      return;
    }
    setMismatch(false);
    reset.mutate();
  }

  const backToLogin = (
    <Anchor component={Link} to="/login" ta="center" size="sm">
      {t("auth.to_login")}
    </Anchor>
  );

  if (reset.isSuccess) {
    return (
      <AuthLayout title={t("auth.reset_title")}>
        <Stack>
          <Text c="teal">{t("auth.reset_done")}</Text>
          {backToLogin}
        </Stack>
      </AuthLayout>
    );
  }

  return (
    <AuthLayout title={t("auth.reset_title")}>
      {login === null ? (
        <form onSubmit={submitKey}>
          <Stack>
            <ErrorAlert error={check.error} />
            <TextInput
              label={t("auth.invite_key")}
              description={t("auth.reset_key_hint")}
              value={key}
              onChange={(e) => setKey(e.currentTarget.value)}
              autoComplete="off"
              spellCheck={false}
              required
              autoFocus
            />
            <Button type="submit" loading={check.isPending}>
              {t("common.next")}
            </Button>
            {backToLogin}
          </Stack>
        </form>
      ) : (
        <form onSubmit={submitPassword}>
          <Stack>
            <Text size="sm">{t("auth.reset_for", { login })}</Text>
            <ErrorAlert error={reset.error} />
            <PasswordField label={t("auth.new_password")} value={password} onChange={setPassword} userInputs={[login]} />
            <PasswordInput
              label={t("auth.new_password_repeat")}
              value={repeat}
              onChange={(e) => setRepeat(e.currentTarget.value)}
              error={mismatch ? t("auth.passwords_differ") : undefined}
              autoComplete="new-password"
              required
            />
            <Button type="submit" loading={reset.isPending} disabled={!!passwordProblem(password, [login])}>
              {t("auth.reset_submit")}
            </Button>
          </Stack>
        </form>
      )}
    </AuthLayout>
  );
}
