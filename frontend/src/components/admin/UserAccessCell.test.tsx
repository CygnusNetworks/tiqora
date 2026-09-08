import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { I18nextProvider } from "react-i18next";
import i18n from "@/i18n";
import { UserAccessCell } from "./UserAccessCell";
import { accessState } from "@/lib/userAccess";

const NOW = new Date("2026-09-08T16:00:00Z");
const FUTURE = "2026-09-15T15:43:58+00:00";
const PAST = "2026-07-31T10:00:00+00:00";

describe("accessState", () => {
  it("reports an outstanding invitation while its link is still valid", () => {
    expect(
      accessState(
        {
          invited_at: "2026-09-08T15:43:58+00:00",
          invite_expires: FUTURE,
          invite_accepted_at: null,
          last_login: null,
        },
        NOW,
      ),
    ).toBe("invited");
  });

  it("reports an expired invitation once the link has lapsed", () => {
    expect(
      accessState(
        {
          invited_at: "2026-07-24T12:29:00+00:00",
          invite_expires: PAST,
          invite_accepted_at: null,
          last_login: null,
        },
        NOW,
      ),
    ).toBe("expired");
  });

  it("separates 'password set' from 'has actually signed in'", () => {
    const accepted = {
      invited_at: "2026-07-24T12:29:00+00:00",
      invite_expires: PAST,
      invite_accepted_at: "2026-07-25T09:00:00+00:00",
      last_login: null,
    };
    expect(accessState(accepted, NOW)).toBe("accepted");
    expect(accessState({ ...accepted, last_login: "2026-09-07T09:04:00+00:00" }, NOW)).toBe(
      "active",
    );
  });

  it("treats a signed-in account without an invitation as active, not 'no invitation'", () => {
    // Service accounts predate the invite flow but are plainly in use — the
    // label names the furthest point reached, not how the account was created.
    expect(
      accessState(
        {
          invited_at: null,
          invite_expires: null,
          invite_accepted_at: null,
          last_login: "2026-09-07T09:04:00+00:00",
        },
        NOW,
      ),
    ).toBe("active");
  });

  it("falls back to 'no invitation' for an account that was never invited or used", () => {
    expect(
      accessState(
        { invited_at: null, invite_expires: null, invite_accepted_at: null, last_login: null },
        NOW,
      ),
    ).toBe("none");
  });
});

describe("UserAccessCell", () => {
  it("shows the state and the date that state makes actionable", () => {
    render(
      <I18nextProvider i18n={i18n}>
        <UserAccessCell
          user={{
            invited_at: "2026-09-08T15:43:58+00:00",
            invite_expires: FUTURE,
            invite_accepted_at: null,
            last_login: null,
          }}
        />
      </I18nextProvider>,
    );
    expect(screen.getByTestId("user-access-invited")).toBeTruthy();
    expect(screen.getByText(/link expires/i)).toBeTruthy();
  });

  it("tells an admin the account has never been used once the password is set", () => {
    render(
      <I18nextProvider i18n={i18n}>
        <UserAccessCell
          user={{
            invited_at: "2026-07-24T12:29:00+00:00",
            invite_expires: PAST,
            invite_accepted_at: "2026-07-25T09:00:00+00:00",
            last_login: null,
          }}
        />
      </I18nextProvider>,
    );
    expect(screen.getByTestId("user-access-accepted")).toBeTruthy();
    expect(screen.getByText(/never signed in/i)).toBeTruthy();
  });
});
