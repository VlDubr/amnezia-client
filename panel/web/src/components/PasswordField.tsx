import { PasswordInput, Text } from "@mantine/core";
import { useTranslation } from "react-i18next";
import { passwordProblem } from "../lib/password";

export { passwordProblem };

type Props = {
  label: string;
  value: string;
  onChange: (value: string) => void;
  userInputs?: string[];
  autoComplete?: string;
};

/** Password input that explains the policy while the user types. */
export function PasswordField({ label, value, onChange, userInputs = [], autoComplete = "new-password" }: Props) {
  const { t } = useTranslation();
  const problem = value ? passwordProblem(value, userInputs) : null;
  return (
    <div>
      <PasswordInput
        label={label}
        value={value}
        onChange={(e) => onChange(e.currentTarget.value)}
        autoComplete={autoComplete}
        required
      />
      <Text size="xs" mt={4} c={!value ? "dimmed" : problem ? "red" : "teal"}>
        {!value ? t("password.rules") : problem ? t(`password.${problem}`) : t("password.ok")}
      </Text>
    </div>
  );
}
