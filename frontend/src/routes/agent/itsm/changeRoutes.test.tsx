import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { I18nextProvider } from "react-i18next";
import i18n from "@/i18n";
import { ChangeDetailPage } from "./ChangeDetailPage";
import { ChangeCalendarPage } from "./ChangeCalendarPage";
import { CabMeetingsPage } from "./CabMeetingsPage";
import { CabMeetingPage } from "./CabMeetingPage";
import { LegacyChangeListPage } from "./LegacyChangeListPage";
import { LegacyChangeDetailPage } from "./LegacyChangeDetailPage";

beforeEach(async () => {
  await i18n.changeLanguage("de");
});
afterEach(cleanup);

describe("change route stubs", () => {
  it.each([
    [ChangeDetailPage, "Change"],
    [ChangeCalendarPage, "Change-Kalender"],
    [CabMeetingsPage, "CAB-Sitzungen"],
    [CabMeetingPage, "CAB-Sitzung"],
    [LegacyChangeListPage, "Changes (Znuny)"],
    [LegacyChangeDetailPage, "Znuny-Change"],
  ])("renders the title of %o", (Page, title) => {
    render(
      <I18nextProvider i18n={i18n}>
        <Page />
      </I18nextProvider>,
    );
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent(title);
  });
});
