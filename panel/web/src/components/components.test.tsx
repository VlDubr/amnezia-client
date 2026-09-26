import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { renderUi } from "../../test/ui";
import { PasswordField, passwordProblem } from "./PasswordField";
import { ShareModal } from "./ShareModal";
import { StatusBadge } from "./StatusBadge";

const EXPORT = {
  vpn_key: "vpn://AAAA",
  native: "[Interface]\nPrivateKey = x\n",
  native_filename: "nl-1.conf",
  qr_svg: '<svg xmlns="http://www.w3.org/2000/svg" data-testid="qr"></svg>',
};

describe("ShareModal", () => {
  it("shows the key, the native config, download and QR", () => {
    renderUi(<ShareModal opened name="Phone" data={EXPORT} onClose={() => {}} />);
    expect(screen.getByText("Конфиг «Phone»")).toBeInTheDocument();
    expect(screen.getByDisplayValue("vpn://AAAA")).toBeInTheDocument();
    expect(screen.getByText(/PrivateKey = x/)).toBeInTheDocument();
    const link = screen.getByRole("link", { name: /Скачать/ });
    expect(link).toHaveAttribute("download", "nl-1.conf");
    expect(screen.getByTestId("qr")).toBeInTheDocument();
    expect(screen.getByTestId("qr").parentElement).toHaveClass("panel-qr"); // CSS scales the SVG to the box
  });

  it("does not render a QR that is not an SVG document", () => {
    renderUi(<ShareModal opened name="x" data={{ ...EXPORT, qr_svg: "<img src=x onerror=alert(1)>" }} onClose={() => {}} />);
    expect(document.querySelector("img")).toBeNull();
  });

  it("shows a service link instead of a vpn:// key", () => {
    const link = "tg://proxy?server=h&port=443&secret=ee00";
    renderUi(<ShareModal opened name="TG" data={{ ...EXPORT, vpn_key: "", native: link }} onClose={() => {}} />);
    expect(screen.queryByLabelText("Ключ для приложения AmneziaVPN")).toBeNull();
    expect(screen.getByDisplayValue(link)).toBeInTheDocument();
    expect(screen.getByTestId("qr")).toBeInTheDocument();
  });

  it("explains when a config cannot be issued again", () => {
    renderUi(<ShareModal opened name="old" data={null} onClose={() => {}} />);
    expect(screen.getByText(/импортирован с сервера/)).toBeInTheDocument();
  });
});

describe("password policy", () => {
  it("matches the server rules", () => {
    expect(passwordProblem("short")).toBe("too_short");
    expect(passwordProblem("aaaaaaaaaaaa")).toBe("too_weak");
    expect(passwordProblem("Vq7#mZ2!rT9p@Lx")).toBeNull();
  });

  it("shows the rule while typing", async () => {
    const typed: string[] = [];
    const { rerender } = renderUi(<PasswordField label="Пароль" value="" onChange={(v) => typed.push(v)} />);
    expect(screen.getByText(/Не короче 12 символов и не угадываемый/)).toBeInTheDocument();
    await userEvent.type(screen.getByLabelText(/Пароль/), "s");
    expect(typed).toEqual(["s"]);
    rerender(<PasswordField label="Пароль" value="short" onChange={() => {}} />);
    expect(screen.getByText("Не короче 12 символов")).toBeInTheDocument();
    rerender(<PasswordField label="Пароль" value="Vq7#mZ2!rT9p@Lx" onChange={() => {}} />);
    expect(screen.getByText("Надёжный пароль")).toBeInTheDocument();
  });
});

describe("StatusBadge", () => {
  it("labels every status", () => {
    renderUi(
      <>
        <StatusBadge status="active" />
        <StatusBadge status="expired" />
        <StatusBadge status="blocked" blockedBy="admin" />
        <StatusBadge status="blocked" blockedBy="user" />
      </>,
    );
    expect(screen.getByText("Активен")).toBeInTheDocument();
    expect(screen.getByText("Срок истёк")).toBeInTheDocument();
    expect(screen.getByText("Заблокирован администратором")).toBeInTheDocument();
    expect(screen.getByText("Заблокирован вами")).toBeInTheDocument();
  });
});
