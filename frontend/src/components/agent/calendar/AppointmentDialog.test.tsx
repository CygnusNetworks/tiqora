import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { I18nextProvider } from "react-i18next";
import i18n from "@/i18n";
import { AppointmentDialog } from "./AppointmentDialog";
import { defaultFormValue, formValueFromAppointment } from "./appointmentForm";
import type { AppointmentOut, CalendarOut } from "@/lib/api";

const calendars: CalendarOut[] = [
  { id: 1, name: "Team", color: "#000", group_id: 1 } as CalendarOut,
  { id: 2, name: "Personal", color: "#111", group_id: 1 } as CalendarOut,
];

function wrap(ui: React.ReactElement) {
  return render(<I18nextProvider i18n={i18n}>{ui}</I18nextProvider>);
}

describe("AppointmentDialog", () => {
  it("renders create mode with the new-appointment title and no delete button", () => {
    wrap(
      <AppointmentDialog
        open
        onClose={vi.fn()}
        calendars={calendars}
        initial={defaultFormValue(1)}
        editing={false}
        onSave={vi.fn()}
      />,
    );
    expect(screen.getByText("New appointment")).toBeInTheDocument();
    expect(screen.queryByTestId("appointment-delete")).not.toBeInTheDocument();
  });

  it("renders edit mode with the edit title and a delete button wired to onDelete", () => {
    const onDelete = vi.fn();
    const appt: AppointmentOut = {
      id: 5,
      calendar_id: 1,
      title: "Standup",
      description: "",
      location: "",
      start_time: "2026-01-01T09:00:00",
      end_time: "2026-01-01T09:30:00",
      all_day: false,
      recur_type: null,
      recur_interval: null,
      recur_count: null,
      recur_until: null,
    } as AppointmentOut;

    wrap(
      <AppointmentDialog
        open
        onClose={vi.fn()}
        calendars={calendars}
        initial={formValueFromAppointment(appt)}
        editing
        onSave={vi.fn()}
        onDelete={onDelete}
      />,
    );
    expect(screen.getByText("Edit appointment")).toBeInTheDocument();
    expect(screen.getByTestId("appointment-title")).toHaveValue("Standup");

    fireEvent.click(screen.getByTestId("appointment-delete"));
    expect(onDelete).toHaveBeenCalledTimes(1);
  });

  it("does not render a delete button in edit mode without an onDelete handler", () => {
    wrap(
      <AppointmentDialog
        open
        onClose={vi.fn()}
        calendars={calendars}
        initial={defaultFormValue(1)}
        editing
        onSave={vi.fn()}
      />,
    );
    expect(screen.queryByTestId("appointment-delete")).not.toBeInTheDocument();
  });

  it("requires title, start and end before the browser lets the form submit", () => {
    const onSave = vi.fn();
    wrap(
      <AppointmentDialog
        open
        onClose={vi.fn()}
        calendars={calendars}
        initial={defaultFormValue(1)}
        editing={false}
        onSave={onSave}
      />,
    );
    expect(screen.getByTestId("appointment-title")).toBeRequired();
    expect(screen.getByTestId("appointment-start")).toBeRequired();
    expect(screen.getByTestId("appointment-end")).toBeRequired();
  });

  it("submits the edited form value including calendar/title/time changes", () => {
    const onSave = vi.fn();
    wrap(
      <AppointmentDialog
        open
        onClose={vi.fn()}
        calendars={calendars}
        initial={defaultFormValue(1)}
        editing={false}
        onSave={onSave}
      />,
    );

    fireEvent.change(screen.getByTestId("appointment-title"), {
      target: { value: "Kickoff meeting" },
    });
    fireEvent.change(screen.getByTestId("appointment-start"), {
      target: { value: "2026-02-01T10:00" },
    });
    fireEvent.change(screen.getByTestId("appointment-end"), {
      target: { value: "2026-02-01T11:00" },
    });
    fireEvent.change(screen.getByTestId("appointment-location"), {
      target: { value: "Room 1" },
    });

    fireEvent.submit(screen.getByTestId("appointment-form"));

    expect(onSave).toHaveBeenCalledTimes(1);
    const submitted = onSave.mock.calls[0][0];
    expect(submitted.title).toBe("Kickoff meeting");
    expect(submitted.start_time).toBe("2026-02-01T10:00");
    expect(submitted.end_time).toBe("2026-02-01T11:00");
    expect(submitted.location).toBe("Room 1");
  });

  it("shows the recurrence interval/count/until fields only once a recurrence type is picked", () => {
    wrap(
      <AppointmentDialog
        open
        onClose={vi.fn()}
        calendars={calendars}
        initial={defaultFormValue(1)}
        editing={false}
        onSave={vi.fn()}
      />,
    );
    expect(screen.queryByTestId("appointment-recurrence-interval")).not.toBeInTheDocument();

    fireEvent.click(screen.getByTestId("appointment-recurrence-type"));
    fireEvent.click(screen.getByText("Weekly"));

    expect(screen.getByTestId("appointment-recurrence-interval")).toBeInTheDocument();
    expect(screen.getByTestId("appointment-recurrence-count")).toBeInTheDocument();
    expect(screen.getByTestId("appointment-recurrence-until")).toBeInTheDocument();
  });

  it("includes the chosen recurrence in the submitted payload", () => {
    const onSave = vi.fn();
    wrap(
      <AppointmentDialog
        open
        onClose={vi.fn()}
        calendars={calendars}
        initial={{ ...defaultFormValue(1), title: "Weekly sync" }}
        editing={false}
        onSave={onSave}
      />,
    );

    fireEvent.click(screen.getByTestId("appointment-recurrence-type"));
    fireEvent.click(screen.getByText("Weekly"));

    fireEvent.change(screen.getByTestId("appointment-recurrence-interval"), {
      target: { value: "2" },
    });
    fireEvent.change(screen.getByTestId("appointment-recurrence-count"), {
      target: { value: "5" },
    });

    fireEvent.submit(screen.getByTestId("appointment-form"));

    expect(onSave).toHaveBeenCalledTimes(1);
    const submitted = onSave.mock.calls[0][0];
    expect(submitted.recurrence).toEqual(
      expect.objectContaining({ type: "Weekly", interval: 2, count: "5" }),
    );
  });

  it("falls back to interval 1 for an invalid (non-numeric) interval entry", () => {
    wrap(
      <AppointmentDialog
        open
        onClose={vi.fn()}
        calendars={calendars}
        initial={defaultFormValue(1)}
        editing={false}
        onSave={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByTestId("appointment-recurrence-type"));
    fireEvent.click(screen.getByText("Weekly"));

    fireEvent.change(screen.getByTestId("appointment-recurrence-interval"), {
      target: { value: "" },
    });
    expect(screen.getByTestId("appointment-recurrence-interval")).toHaveValue(1);
  });

  it("disables the submit button while saving and shows the error message", () => {
    wrap(
      <AppointmentDialog
        open
        onClose={vi.fn()}
        calendars={calendars}
        initial={defaultFormValue(1)}
        editing={false}
        onSave={vi.fn()}
        saving
        error="Could not save appointment"
      />,
    );
    expect(screen.getByTestId("appointment-form-submit")).toBeDisabled();
    expect(screen.getByTestId("appointment-form-error")).toHaveTextContent(
      "Could not save appointment",
    );
  });

  it("calls onClose when the cancel button is clicked", () => {
    const onClose = vi.fn();
    wrap(
      <AppointmentDialog
        open
        onClose={onClose}
        calendars={calendars}
        initial={defaultFormValue(1)}
        editing={false}
        onSave={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByText("Cancel"));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("resets the form to the new initial value when reopened for a different appointment", () => {
    const { rerender } = render(
      <I18nextProvider i18n={i18n}>
        <AppointmentDialog
          open
          onClose={vi.fn()}
          calendars={calendars}
          initial={{ ...defaultFormValue(1), title: "First" }}
          editing
          onSave={vi.fn()}
        />
      </I18nextProvider>,
    );
    expect(screen.getByTestId("appointment-title")).toHaveValue("First");

    rerender(
      <I18nextProvider i18n={i18n}>
        <AppointmentDialog
          open
          onClose={vi.fn()}
          calendars={calendars}
          initial={{ ...defaultFormValue(1), title: "Second" }}
          editing
          onSave={vi.fn()}
        />
      </I18nextProvider>,
    );
    expect(screen.getByTestId("appointment-title")).toHaveValue("Second");
  });
});
