---
name: tiqora-notification-sender-breaks-sieve-sorting
description: |
  Ticket-system notification mails suddenly land in the wrong IMAP folder (or
  in personal inboxes) although their content is fine. Use when: (1) after a
  Znuny/OTRS -> Tiqora migration the "watcher"/BCC archive folder stops
  filling while the mails clearly still arrive, (2) users report "I suddenly
  get notifications I never got before" without any change to notification
  config or recipients, (3) a notification mail has no Date and no Message-ID
  header at all, (4) Message-IDs of outgoing mail carry a docker container id
  as their domain (@7365fc6ef05a), (5) notification subjects lost the
  [Cygnus#...]/[Ticket#...] hook, (6) Tiqora logs
  notification_sender_falls_back_to_queue_address. Root cause is almost never
  the notification engine: it is the From address, because server-side sieve
  rules branch on it. Also carries the full Znuny "Loop => 1" parity checklist
  for automated mail, and the inbound counterpart: Znuny turns Precedence /
  X-Loop / Auto-Submitted into X-OTRS-Loop, and drops x-otrs* headers from
  untrusted mailboxes, so a port that only reads X-OTRS-Loop has no working
  auto-response loop protection at all.
author: Claude Code
version: 1.1.0
date: 2026-09-10
---

# Notification From address decides which folder mail lands in

## Problem

Tiqora's notification engine resolves the `From:` address as

1. `NotificationSenderEmail` (Znuny SysConfig) — **but only when it holds a
   real address**, and
2. otherwise the **system address of the ticket's queue**.

Znuny ships `NotificationSenderEmail` as the unexpanded placeholder
`otrs@<OTRS_CONFIG_FQDN>`, and the real `FQDN` usually lives only in Znuny's
`Config.pm`, which never reaches the `sysconfig_default`/`sysconfig_modified`
tables Tiqora reads (see `znuny-configpm-bypasses-sysconfig-db`). So the
fallback fires and notifications go out **as the queue's own address** — the
same address ordinary ticket correspondence uses.

Any downstream mail filter that distinguishes "system notification" from
"ticket reply" by sender then silently re-files every notification.

## Context / Trigger conditions

- The BCC archive folder (`SendmailBcc`, e.g. `otrs-watcher@…`) stops
  receiving mail on a specific date, and the last mail in it predates the
  Tiqora release that changed the notification sender.
- Users see notifications "for other people" appear in a folder they read.
  They were always being sent — they used to be filed into an archive folder
  nobody reads, because the *personal* copy carries the archive address in a
  second `X-Original-To` header and sieve's `header :is` matches **any** value
  of a repeated header.
- No config, no recipient list and no `personal_queues` row changed.

## Diagnosis

1. Get the raw mail. Two `X-Original-To` headers (personal + archive address)
   are the norm, not a bug.
2. Find the filter, not the sender:
   ```sh
   grep -rn "<archive-address>" /srv/mail-config/     # dovecot/sieve configs
   cat /srv/mail-config/before.dovecot.sieve          # domain dispatch
   cat /srv/mail-config/sieve/<domain>.sieve          # per-address rules
   ```
   Look for `if address :is "from" "…"` **inside** the archive-address branch.
3. Prove where the mail went, by its Maildir filename timestamp (epoch):
   ```sh
   # the mail's Received: date -> epoch -> filename prefix
   ls -t /srv/mail-data/shared/<Folder>/cur | head
   grep -l "Tiqora Notifications" /srv/mail-data/shared/<OtherFolder>/cur/* | head
   ```
4. Confirm the sender change is the only delta:
   ```sql
   SELECT name, effective_value FROM sysconfig_modified WHERE name = 'FQDN';
   -- no row => Tiqora sees 'yourhost.example.com', not the real FQDN
   ```

## Fix

Set an explicit sender so notifications never borrow the queue address:

```sql
INSERT INTO tiqora_settings (`key`, value) VALUES
  ('notification.sender_email', 'noreply@tiqora.example.com'),
  ('notification.sender_name',  'Tiqora Notifications')
ON DUPLICATE KEY UPDATE value = VALUES(value);
```

Do **not** "fix" this by expanding `<OTRS_CONFIG_FQDN>` against the Tiqora web
host — that invents a mailbox nobody reads. Writing Znuny's
`NotificationSenderEmail` via `Admin::Config::Update` also works but touches
shared config in parallel operation.

## Znuny parity checklist for automated mail

Znuny's `Kernel::System::Email::Send` with `Loop => 1` (notifications **and**
auto-responses, `Article/Backend/Email.pm:692`) writes all of this. A port that
only sets `X-OTRS-Loop` is missing:

| Header / behaviour | Znuny source |
|---|---|
| `[Hook#tn]` in the subject | `TemplateGenerator.pm:1147` → `TicketSubjectBuild(Type => 'New')` |
| `Date`, `Message-ID` | `Email.pm:1120-1129` — RFC 5322 requires both |
| `Organization` | `Email.pm:1132` (SysConfig `Organization`) |
| `X-Loop: yes`, `Precedence: bulk`, `Auto-Submitted: auto-generated` | `Email.pm:1045-1048` |
| null envelope sender (`Return-Path: <>`) | `Email.pm:622` — `SendmailNotificationEnvelopeFrom`, empty by default, `::FallbackToEmailFrom` off |

`EmailMessage` + `aiosmtplib` add **none** of these on their own — not even
`Date`. `aiosmtplib.send(message, sender="")` is how you get the null envelope
sender (an empty string survives; only `None` triggers From-header extraction).

Derive generated Message-ID domains from the `From` address, never from
`socket.getfqdn()`: inside a container that yields the container id, which
changes on every restart and resolves nowhere.

## The inbound counterpart (easy to miss)

`PostMaster.pm:581` drops **every** `x-otrs*` header when the mail account is
not trusted — the normal case. `PostMaster.pm:602-614` then re-derives the flag:

```perl
if ( $GetParam{'Mailing-List'} || $GetParam{'Precedence'} || $GetParam{'X-Loop'}
     || $GetParam{'X-No-Loop'} || $GetParam{'X-OTRS-Loop'}
     || ( $GetParam{'Auto-Submitted'} && substr($_, 0, 5) eq 'auto-' ) )
{ $GetParam{'X-OTRS-Loop'} = 'yes'; }
```

A port that drops `x-otrs*` for untrusted mail (correct) but never re-derives
the flag has **no working loop protection**: an out-of-office reply carrying
only `Auto-Submitted: auto-replied` — the standard header, the one Znuny itself
sends — gets an auto-response back, and the two systems answer each other.

Related trap in the same function: if inbound header keys are normalized by
`"-".join(p.capitalize() ...)`, `x-otrs-loop` becomes `X-Otrs-Loop`, so every
`get_param["X-OTRS-Queue"]`-style lookup silently misses real headers and only
ever sees values injected by PostMaster filters.

## Verification

- `grep -c . <archive>/cur` grows again after the next notification.
- The mail carries `Date`, `Message-ID`, `X-Loop`, `Precedence`,
  `Auto-Submitted` and the hooked subject.
- `Return-Path: <>` in the delivered copy.

## Notes

- Recipients like `AgentMyQueues` come from `personal_queues`. If the real ask
  is "these people should not get these mails at all", the lever is that table
  or the notification's `Recipients`/`VisibleForAgent` items — **not** the
  sender. Znuny additionally honours a per-agent opt-out preference
  `Notification-<ID>-Email`, which Tiqora does not implement (harmless while no
  such rows exist; check before assuming an opt-out is respected).
- Znuny also gates every agent recipient on `ro` permission for the ticket
  (`NotificationEvent.pm:1120`) — worth checking if a port over-notifies.
- See also: `znuny-configpm-bypasses-sysconfig-db`, `dms-rspamd-multimap-config`.
