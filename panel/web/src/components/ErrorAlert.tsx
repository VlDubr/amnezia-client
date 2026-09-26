import { Alert } from "@mantine/core";
import { errorText } from "../i18n";

export function ErrorAlert({ error }: { error: unknown }) {
  if (!error) return null;
  return (
    <Alert color="red" variant="light" role="alert">
      {errorText(error)}
    </Alert>
  );
}
