---
name: tiqora-newticket-fieldset-locks-tests
description: |
  Fix for a Tiqora frontend test failing with "Received element is not enabled"
  on a control inside NewTicketPage, although the element renders and its own
  props say it should be enabled. Use when: (1) a vitest/testing-library test
  on frontend/src/routes/agent/NewTicketPage.tsx asserts toBeEnabled()/click on
  a button or input and fails even though the component has disabled={false},
  (2) the printed element in the failure has NO disabled attribute of its own,
  (3) fireEvent.change on new-ticket-body (or any other form field) appears to
  do nothing and downstream state stays empty. Root cause: the whole form sits
  in <fieldset disabled={!formUnlocked}>, and formUnlocked requires a customer
  to be picked first — the fix is calling the existing pickCustomer() helper,
  not changing the component.
author: Claude Code
version: 1.0.0
date: 2026-09-15
---

# Tiqora NewTicketPage: a disabled fieldset silently locks every field in tests

## Problem

A test renders `NewTicketPage`, types into a field, and asserts a control is
enabled. It fails with:

```
Received element is not enabled:
  <button
    class="... disabled:opacity-50 disabled:pointer-events-none ..."
    data-testid="new-ticket-refine-button"
    title="Rewrites only what you typed; quoted text is left untouched."
    type="button"
  />
```

The failure is misleading twice over:

- The printed element carries **no `disabled` attribute of its own**, so the
  component looks innocent.
- Attributes that depend on the component's *internal* state can prove it is
  not self-disabled. Above, the `title` is the "there is text to work on"
  branch — i.e. the component computed `canRefine === true` and still rendered
  as disabled.

`toBeEnabled()` (and real clicks) walk up the tree: **an ancestor
`<fieldset disabled>` disables every form control inside it**, per HTML, with
no attribute on the descendant.

## Context / Trigger Conditions

- File under test: `frontend/src/routes/agent/NewTicketPage.tsx`
- Any `expect(...).toBeEnabled()`, `fireEvent.click`, or `fireEvent.change` on
  a control inside the main form
- The test called `renderReady()` but **not** `pickCustomer()`
- Symptom variant: `fireEvent.change(screen.getByTestId("new-ticket-body"), …)`
  seems to succeed but the value never lands in state, so anything derived
  from the body stays empty

## Root cause

`NewTicketPage.tsx` wraps the whole form:

```tsx
<fieldset
  disabled={!formUnlocked}
  className={cn("…", !formUnlocked && "pointer-events-none opacity-50")}
>
```

`formUnlocked` only becomes true once a customer is selected (or the
"skip customer" path is taken). Until then every input, textarea and button in
the form is inert — including newly added controls, which is how this bites
someone adding a feature rather than touching the lock logic.

## Solution

Use the helper the test file already defines, right after `renderReady()`:

```tsx
await renderReady();
// The form stays locked (disabled fieldset) until a customer is picked.
await pickCustomer();

fireEvent.change(screen.getByTestId("new-ticket-body"), { target: { value: "…" } });
await waitFor(() => expect(screen.getByTestId("new-ticket-refine-button")).toBeEnabled());
```

`pickCustomer()` types into `new-ticket-customer-search`, waits for
`searchReferenceCustomers`, and clicks `new-ticket-customer-result-<login>`.
It needs `searchReferenceCustomers` mocked in that test's `beforeEach` — a new
`describe` block must repeat the mock setup, it is not inherited.

Do **not** "fix" this by removing the `disabled` from the fieldset or adding an
escape hatch to the component: the lock is deliberate product behaviour.

## Verification

```sh
cd frontend && npx vitest run src/routes/agent/NewTicketPage.test.tsx
```

All tests in the file pass; the previously failing assertion now finds an
enabled control.

## Notes

- Generalises beyond Tiqora: whenever testing-library reports "element is not
  enabled" for an element with no `disabled` attribute of its own, look for an
  ancestor `<fieldset disabled>` (or `inert`) before suspecting the component.
- The `pointer-events-none` class hides the same problem in manual clicking,
  but `fireEvent` bypasses pointer-events — so the fieldset, not the class, is
  what actually blocks the test.
- Same trap applies to any future composer control added inside this form.

## References

- HTML spec, disabled fieldset: https://html.spec.whatwg.org/multipage/form-elements.html#attr-fieldset-disabled
- jest-dom `toBeEnabled`: https://github.com/testing-library/jest-dom#tobeenabled
