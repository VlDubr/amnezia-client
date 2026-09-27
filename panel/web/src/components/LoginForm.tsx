import { Button, PasswordInput, Stack, TextInput } from "@mantine/core";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { useLocation, useNavigate } from "react-router";
import { api } from "../api/client";
import type { SessionInfo } from "../api/types";
import { SESSION_KEY, loadSession, nextPath } from "../auth/session";
import { ErrorAlert } from "./ErrorAlert";

/** One sign-in for every account. The role comes from the server's session, never from this form. */
export function LoginForm() {
  const { t } = useTranslation();
  const [login, setLogin] = useState("");
  const [password, setPassword] = useState("");
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const location = useLocation();

  const mutation = useMutation({
    mutationFn: () => api("/api/auth/login", { method: "POST", body: { login, password } }),
    onSuccess: async () => {
      queryClient.removeQueries({ predicate: (q) => q.queryKey[0] !== SESSION_KEY[0] });
      await loadSession(queryClient);
      const session = queryClient.getQueryData<SessionInfo>(SESSION_KEY);
      navigate(nextPath(location.search, session?.role ?? "user"), { replace: true });
    },
  });

  function submit(e: FormEvent) {
    e.preventDefault();
    mutation.mutate();
  }

  return (
    <form onSubmit={submit}>
      <Stack>
        <ErrorAlert error={mutation.error} />
        <TextInput
          label={t("auth.login")}
          value={login}
          onChange={(e) => setLogin(e.currentTarget.value)}
          autoComplete="username"
          required
          autoFocus
        />
        <PasswordInput
          label={t("auth.password")}
          value={password}
          onChange={(e) => setPassword(e.currentTarget.value)}
          autoComplete="current-password"
          required
        />
        <Button type="submit" loading={mutation.isPending}>
          {t("auth.sign_in")}
        </Button>
      </Stack>
    </form>
  );
}
