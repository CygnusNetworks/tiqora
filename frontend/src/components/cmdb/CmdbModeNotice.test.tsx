import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { I18nextProvider } from "react-i18next";
import i18n from "@/i18n";
import { CmdbModeNotice } from "./CmdbModeNotice";

describe("CmdbModeNotice", () => {
  it("explains Znuny mode in German", async () => {
    await i18n.changeLanguage("de");
    render(
      <I18nextProvider i18n={i18n}>
        <CmdbModeNotice status={{ mode: "znuny_readthrough" }} />
      </I18nextProvider>,
    );
    expect(screen.getByText(/zurzeit in Znuny geführt/)).toBeInTheDocument();
  });

  it("renders nothing in Tiqora mode", () => {
    const { container } = render(<CmdbModeNotice status={{ mode: "tiqora" }} />);
    expect(container).toBeEmptyDOMElement();
  });
});
