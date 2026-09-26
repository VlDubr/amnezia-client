import { Anchor, Button, PasswordInput, Stack, Text, TextInput } from "@mantine/core";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { Link, useNavigate } from "react-router";
import { api } from "../../api/client";
import { loadSession } from "../../auth/session";
import { AuthLayout } from "../../components/AuthLayout";
import { ErrorAlert } from "../../components/ErrorAlert";
import { PasswordField, passwordProblem } from "../../components/PasswordField";

/** Б.2: the user enters the invite key, then chooses a login and password. */
export default function InvitePage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [key, setKey] = useState("");
  const [keyChecked, setKeyChecked] = useState(false);
  const [login, setLogin] = useState("");
  const [password, setPassword] = useState("");
  const [repeat, setRepeat] = useState("");
  const [mismatch, setMismatch] = useState(false);

  const check = useMutation({
    mutationFn: () => api("/api/auth/invite/check", { method: "POST", body: { key } }),
    onSuccess: () => setKeyChecked(true),
  });
  const redeem = useMutation({
    mutationFn: () => api("/api/auth/invite/redeem", { method: "POST", body: { key, login, password } }),
    onSuccess: async () => {
      await loadSession(queryClient);
      navigate("/", { replace: true });
    },
  });

  function submitKey(e: FormEvent) {
    e.preventDefault();
    check.mutate();
  }

  function submitCredentials(e: FormEvent) {
    e.preventDefault();
    if (password !== repeat) {
      setMismatch(true);
      return;
    }
    setMismatch(false);
    redeem.mutate();
  }

  return (
    <AuthLayout title={t("auth.invite_title")}>
      {!keyChecked ? (
        <form onSubmit={submitKey}>
          <Stack>
            <ErrorAlert error={check.error} />
            <TextInput
              label={t("auth.invite_key")}
              description={t("auth.invite_key_hint")}
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
            <Anchor component={Link} to="/login" ta="center" size="sm">
              {t("auth.back_to_login")}
            </Anchor>
          </Stack>
        </form>
      ) : (
        <form onSubmit={submitCredentials}>
          <Stack>
            <Text size="sm">{t("auth.invite_set_credentials")}</Text>
            <ErrorAlert error={redeem.error} />
            <TextInput
              label={t("auth.login")}
              description={t("auth.login_rules")}
              value={login}
              onChange={(e) => setLogin(e.currentTarget.value.trim())}
              autoComplete="username"
              required
              autoFocus
            />
            <PasswordField label={t("auth.password")} value={password} onChange={setPassword} userInputs={[login]} />
            <PasswordInput
              label={t("auth.password_repeat")}
              value={repeat}
              onChange={(e) => setRepeat(e.currentTarget.value)}
              error={mismatch ? t("auth.passwords_differ") : undefined}
              autoComplete="new-password"
              required
            />
            <Button type="submit" loading={redeem.isPending} disabled={!!passwordProblem(password, [login])}>
              {t("auth.register")}
            </Button>
          </Stack>
        </form>
      )}
    </AuthLayout>
  );
}
