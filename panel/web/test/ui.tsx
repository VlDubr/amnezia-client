import { MantineProvider } from "@mantine/core";
import { ModalsProvider } from "@mantine/modals";
import { render } from "@testing-library/react";
import type { ReactNode } from "react";
import "../src/i18n";

function Providers({ children }: { children: ReactNode }) {
  return (
    <MantineProvider>
      <ModalsProvider>{children}</ModalsProvider>
    </MantineProvider>
  );
}

export function renderUi(node: ReactNode) {
  return render(node, { wrapper: Providers });
}
