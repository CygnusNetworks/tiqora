"""Curated realistic IT-helpdesk ticket content for ``tiqora dev seed``.

:func:`~tiqora.domain.dev_seed.seed_database` used to draw ticket titles and
article bodies from ``Faker.sentence()``/``Faker.paragraph()`` — grammatical
but content-free lorem-ipsum that makes seeded tickets useless for demoing
search, AI summaries, or the UI in general. This module replaces that with a
hand-written pool of realistic scenarios, in the spirit of
``frontend/src/demo/mockData.ts`` (English, IT-helpdesk domain, specific
enough to mention error text, timestamps, and concrete next steps).

Each :class:`SeedScenario` is a self-contained ticket thread: a title, a
"Category" hint (harmless if no such dynamic field exists in the target
schema — ``update_dynamic_field`` silently ignores unknown field names), and
2-6 articles alternating customer reports/replies with agent responses and
the occasional agent-only internal note.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SeedArticle:
    sender_type: str  # "customer" | "agent"
    is_visible_for_customer: bool
    body: str


@dataclass(frozen=True)
class SeedScenario:
    title: str
    category: str
    priority_hint: str | None
    articles: tuple[SeedArticle, ...]


def _c(body: str) -> SeedArticle:
    """A customer-authored, customer-visible article (report or follow-up)."""
    return SeedArticle("customer", True, body)


def _a(body: str) -> SeedArticle:
    """An agent-authored, customer-visible reply."""
    return SeedArticle("agent", True, body)


def _note(body: str) -> SeedArticle:
    """An agent-authored internal note, not visible to the customer."""
    return SeedArticle("agent", False, body)


SCENARIOS: tuple[SeedScenario, ...] = (
    SeedScenario(
        title="Printer offline in building A, floor 2",
        category="Hardware",
        priority_hint="3 normal",
        articles=(
            _c(
                "Hi, the main printer on floor 2 of building A (HP-LJ-4205, "
                "asset tag IT-0472) has been offline since about 8:30 this "
                "morning. The display shows a blinking amber light and it "
                "doesn't respond to the web interface at 10.20.4.55 either.\n\n"
                "About a dozen people on this floor rely on it for client "
                "paperwork, so this is fairly urgent for us. Could someone "
                "take a look today?"
            ),
            _a(
                "Hi, thanks for reporting this. I just pulled the event log "
                "from the printer's web console remotely — it's throwing "
                "error code 79.0A9C, which on this model usually points to a "
                "firmware fault rather than a paper jam or toner issue.\n\n"
                "I've scheduled a technician to reset the formatter board "
                "on-site within the hour. In the meantime, I've re-routed the "
                "default print queue for floor 2 to the printer in the "
                "adjacent copy room (HP-LJ-4198) so people aren't completely "
                "blocked."
            ),
            _note(
                "Checked SNMP history for this device: three prior 79.0A9C "
                "faults in the last two months, always after a power blip on "
                "that circuit. Flagging for facilities to check the UPS on "
                "that floor — this printer may need to move to a protected "
                "outlet rather than another firmware reset."
            ),
            _a(
                "Update: the on-site reset cleared the fault and the printer "
                "is back online as of 11:15. I also applied the latest "
                "firmware (2.4.11.2) since the vendor changelog mentions a "
                "fix for spurious 79.0A9C errors after brief power loss.\n\n"
                "I've reverted the floor 2 default queue back to HP-LJ-4205. "
                "Please let us know if you see the same error again — if so "
                "we'll escalate to facilities about the power circuit rather "
                "than keep resetting the board."
            ),
            _c(
                "That worked, thank you! Printed a 40-page contract just now "
                "without any issues. Appreciate the quick turnaround."
            ),
        ),
    ),
    SeedScenario(
        title="VPN access request for new remote contractor",
        category="Access",
        priority_hint="3 normal",
        articles=(
            _c(
                "We're onboarding a contractor (Priya Nandakumar, start date "
                "next Monday) who'll be working fully remote for the Q3 "
                "reporting project. Could you set up VPN access for her? "
                "She'll need the finance-readonly group and standard SSL VPN, "
                "nothing more.\n\n"
                "Her personal email for the temporary credentials is on file "
                "with HR (ticket HR-2291), let me know if you need anything "
                "else from our side."
            ),
            _a(
                "Thanks — I've created the AD account (p.nandakumar) and "
                "added it to VPN-Users and Finance-ReadOnly. Client "
                "certificate and connection profile have been emailed to the "
                "address on file, along with setup instructions for the "
                "AnyConnect client.\n\n"
                "One thing to flag: contractor accounts are provisioned with "
                "a 90-day expiry by policy. If the engagement runs longer, "
                "please open a renewal request before day 85 so we can extend "
                "it without a gap in access."
            ),
            _c(
                "Perfect, thank you. She confirmed she received the email and "
                "was able to connect this morning. Noted on the 90-day expiry "
                "— I'll put a reminder in our project tracker."
            ),
        ),
    ),
    SeedScenario(
        title="Cannot log into customer portal — invalid credentials error",
        category="Access",
        priority_hint="3 normal",
        articles=(
            _c(
                "I've been trying to log into the customer portal since "
                "yesterday and keep getting 'Invalid username or password' "
                "even though I'm sure the password is correct — it's the same "
                "one I use for email. I tried resetting it twice via the "
                "'Forgot password' link but the reset email never arrives, "
                "not even in spam.\n\n"
                "My login is j.reyes@northwind.example if that helps."
            ),
            _a(
                "Thanks for the details. I checked the account and see the "
                "issue: your customer_user record was marked invalid (valid_id "
                "= 2) during a bulk cleanup last week — that's why both login "
                "and the reset email are silently failing (the mailer skips "
                "invalid accounts by design).\n\n"
                "I've reactivated the account and manually triggered a "
                "password reset email. Could you check in the next few "
                "minutes and confirm it arrives?"
            ),
            _c(
                "Got it, thank you! Reset email arrived and I was able to set "
                "a new password and log in. All good now."
            ),
            _note(
                "Root cause: the March customer-cleanup script "
                "(scripts/deactivate_stale_customers.pl) used a >90-days "
                "inactivity threshold but didn't account for customers who "
                "only ever call in rather than use the portal. Opened "
                "internal ticket OPS-408 to add a call-history check before "
                "the next run."
            ),
        ),
    ),
    SeedScenario(
        title="Invoice discrepancy for March statement",
        category="Billing",
        priority_hint="3 normal",
        articles=(
            _c(
                "Reviewing our March statement (invoice #INV-2026-0341) and "
                "the total doesn't match what we agreed — we're being billed "
                "for 55 named-user seats but our contract addendum from "
                "February caps us at 45 until the Q3 renewal.\n\n"
                "Could someone check the billing system against the contract "
                "and issue a corrected invoice or credit note? Happy to send "
                "over the signed addendum again if it helps."
            ),
            _a(
                "Thanks for flagging this — you're right, and I can see why: "
                "the seat count in our billing system was updated on Feb 18 "
                "based on actual provisioned licenses (which had crept to 55 "
                "after IT added test accounts), rather than the contractual "
                "cap in the addendum.\n\n"
                "I've forwarded this to billing with the addendum reference. "
                "They'll issue a credit note for the 10-seat overcharge on "
                "March's invoice and I've also asked IT to deprovision the "
                "test accounts so this doesn't recur next month."
            ),
            _note(
                "Confirmed with IT: 10 unused test/QA accounts from the "
                "March migration were left active under the customer's "
                "license pool instead of our internal one. Deprovisioned; "
                "billing credit note CN-2026-0089 issued for €412.50."
            ),
            _a(
                "Update: credit note CN-2026-0089 for €412.50 has been issued "
                "and should appear against your account within 2 business "
                "days. The seat count is now correctly capped at 45 in our "
                "system. Apologies for the inconvenience — let us know if "
                "anything looks off on the next statement."
            ),
            _c(
                "Received the credit note, numbers check out now. Thanks for "
                "sorting this out quickly."
            ),
        ),
    ),
    SeedScenario(
        title="New laptop provisioning for starting employee",
        category="Onboarding",
        priority_hint="3 normal",
        articles=(
            _c(
                "New hire Daniel Osei starts on the 3rd in the Marketing "
                "team. Could you provision a standard laptop for him plus the "
                "usual Marketing software bundle (Adobe CC, Asana desktop, "
                "VPN client)? Manager is Bianca Shah if you need sign-off."
            ),
            _a(
                "Confirmed with Bianca. I've allocated a Dell Latitude 5440 "
                "(asset tag IT-1187) from stock, imaged with the standard "
                "corporate build (Win11 23H2), and queued the Marketing "
                "software bundle to install via Intune on first login.\n\n"
                "Laptop will be at reception with his access badge on the "
                "morning of the 3rd. He'll need to complete MFA enrollment on "
                "day one — I've sent the instructions to his personal email "
                "on file with HR."
            ),
            _note(
                "Intune bundle 'Marketing-Standard-v3' assigned to device "
                "IT-1187. Adobe CC license pulled from the shared pool (42/50 "
                "seats now in use — worth flagging to procurement if "
                "Marketing keeps growing this quarter)."
            ),
            _c(
                "Daniel confirmed he received the laptop and badge and "
                "everything installed correctly on first boot. Thanks for the "
                "smooth setup."
            ),
        ),
    ),
    SeedScenario(
        title="Email delivery delayed to external recipients",
        category="Software",
        priority_hint="4 high",
        articles=(
            _c(
                "Several of us have noticed emails to external addresses "
                "(clients at gmail.com and outlook.com specifically) are "
                "taking anywhere from 20 minutes to 2 hours to arrive, "
                "starting sometime yesterday afternoon. Internal mail seems "
                "fine. This is affecting client-facing replies, please treat "
                "as urgent."
            ),
            _a(
                "Thanks, escalating this as high priority. Initial check of "
                "the mail relay queue (mail01) shows a backlog of ~1,400 "
                "messages with deferred status, mostly 4.7.1 'Temporary "
                "failure' responses. Looks like our sending IP may have hit a "
                "rate limit or reputation issue with one of the big "
                "providers."
            ),
            _note(
                "Confirmed via Google Postmaster Tools: our IP's spam rate "
                "crossed 0.3% yesterday, triggering Gmail's temporary "
                "throttling (deferred, not bounced). Correlates with the "
                "marketing newsletter batch sent at 14:00 to an old list that "
                "hadn't been scrubbed in 6+ months."
            ),
            _a(
                "Update: identified the cause — a newsletter batch sent to a "
                "stale mailing list triggered spam-rate throttling from "
                "Gmail. The backlog has now mostly drained (queue down to "
                "~90 messages, all under 10 minutes old) and delivery times "
                "are back to normal.\n\n"
                "We've asked Marketing to run list hygiene before the next "
                "send, and I've added a monitor on the relay's deferred-queue "
                "depth so we catch this earlier next time."
            ),
            _c(
                "Great, can confirm client replies are going out promptly "
                "again on our end too. Thanks for the fast diagnosis."
            ),
        ),
    ),
    SeedScenario(
        title="Password reset for shared support mailbox",
        category="Access",
        priority_hint="2 low",
        articles=(
            _c(
                "The password for our shared mailbox support@ourcompany.example "
                "expired and none of us on the team have the old one written "
                "down anywhere reliable. Could IT reset it and share the new "
                "one with me and my two colleagues (see CC)?"
            ),
            _a(
                "Reset the password for the shared mailbox and configured it "
                "to never expire (shared mailboxes are exempt from the "
                "standard 90-day policy per our AD group policy, this one had "
                "fallen out of the exemption group after a migration).\n\n"
                "New credentials sent via our secure password-share link, "
                "expiring in 24 hours, to you and the two colleagues you "
                "listed."
            ),
            _c("Got it, thank you — all three of us were able to retrieve and use it."),
        ),
    ),
    SeedScenario(
        title="Website contact form returns a server error",
        category="Software",
        priority_hint="4 high",
        articles=(
            _c(
                "The contact form on our public website (ourcompany.example/"
                "contact) has been returning 'Sorry, something went wrong. "
                "Please try again later.' since this morning. We've "
                "definitely lost at least a couple of inbound leads because "
                "of this — could someone look urgently?"
            ),
            _a(
                "Checked the web server logs and found the root cause: the "
                "form submission handler is throwing "
                "'SMTPConnectError: Connection refused (port 587)' — it "
                "looks like the outbound mail relay it depends on rotated "
                "credentials during last night's maintenance window and the "
                "form's config wasn't updated.\n\n"
                "Updating the relay credentials in the form handler now."
            ),
            _note(
                "Confirmed: mail relay credential rotation (change CHG-1187, "
                "scheduled maintenance) didn't include the contact-form "
                "service account in the notification list. Updating the "
                "runbook so form/integration service accounts are explicitly "
                "checked before future rotations."
            ),
            _a(
                "Fixed — updated credentials and submitted three test "
                "messages through the form myself, all delivered "
                "successfully to the sales inbox within a minute. The form "
                "should be fully working now; sorry for the missed leads."
            ),
            _c(
                "Just tested it myself as well and it worked. Thanks for the "
                "quick fix — will let sales know to check if any of yesterday's "
                "attempts can be recovered from server logs."
            ),
        ),
    ),
    SeedScenario(
        title="Slow database queries on the reporting dashboard",
        category="Software",
        priority_hint="4 high",
        articles=(
            _c(
                "Over the past week the reporting dashboard has gotten "
                "noticeably slower — the monthly sales-by-region report used "
                "to load in a few seconds and now regularly takes over a "
                "minute, sometimes timing out entirely. Nothing changed on "
                "our end that I'm aware of."
            ),
            _a(
                "Thanks for the report. Pulled the slow-query log on the "
                "reporting replica and found the sales_by_region view is "
                "doing a full table scan on the orders table — the index on "
                "(region_id, order_date) appears to have been dropped, likely "
                "during last month's schema migration.\n\n"
                "I'll rebuild the index on the replica during the low-traffic "
                "window tonight (22:00-23:00) to avoid replication lag "
                "issues."
            ),
            _note(
                "Confirmed via migration history: index idx_orders_region_date "
                "was dropped in migration 20260614_orders_cleanup.sql as part "
                "of removing an unrelated column, but the rebuild step for "
                "this specific index was missed from the migration's "
                "'up' script. Filed follow-up to add an index-diff check to "
                "the migration review checklist."
            ),
            _a(
                "Index rebuild completed overnight without issues. Re-ran the "
                "sales-by-region report just now — down to 1.8 seconds, back "
                "to expected performance. Please keep an eye on it over the "
                "next few days and let us know if it regresses."
            ),
            _c(
                "Confirmed on our side too, report loads instantly again. "
                "Thanks for tracking down the root cause."
            ),
        ),
    ),
    SeedScenario(
        title="Request: additional license seats for design team",
        category="Billing",
        priority_hint="3 normal",
        articles=(
            _c(
                "Our design team has grown from 5 to 8 people this quarter "
                "and we're out of Figma seats under our current plan. Could "
                "you add 3 more seats? Budget approval from Bianca Shah is "
                "attached (PDF)."
            ),
            _a(
                "Thanks, approval looks good. I've added 3 seats to the Figma "
                "organization plan effective today — pro-rated cost for the "
                "remainder of the billing cycle will show up on next month's "
                "invoice.\n\n"
                "I've assigned the new seats to the three team members you "
                "listed; they should see access within a few minutes of "
                "their next login."
            ),
            _c(
                "All three confirmed they now have full editor access. "
                "Thanks for the quick turnaround."
            ),
        ),
    ),
    SeedScenario(
        title="Two-factor authenticator app not accepting codes",
        category="Access",
        priority_hint="3 normal",
        articles=(
            _c(
                "My authenticator app (Microsoft Authenticator) suddenly "
                "stopped accepting — every code it generates gets rejected as "
                "'invalid or expired' even though I'm typing it in "
                "immediately after it refreshes. This started after I "
                "restored my phone from a backup yesterday."
            ),
            _a(
                "This is a classic time-drift issue after a phone restore — "
                "TOTP codes are time-based and if the phone's clock is off by "
                "more than ~30-60 seconds from our server, every code fails "
                "even though it looks correct.\n\n"
                "Could you check that automatic date & time is enabled in "
                "your phone's settings (rather than manually set)? That "
                "usually resolves it immediately."
            ),
            _c(
                "You were right — automatic time was somehow toggled off "
                "during the restore. Switched it back on and codes are being "
                "accepted normally now. Thanks for the quick diagnosis, "
                "wouldn't have thought to check that."
            ),
        ),
    ),
    SeedScenario(
        title="Onboarding checklist for new starter — access not ready",
        category="Onboarding",
        priority_hint="4 high",
        articles=(
            _c(
                "Our new starter (Elena Vasquez, Customer Success) begins "
                "today but her AD account, email, and CRM access still aren't "
                "provisioned. Her manager says the onboarding ticket was "
                "submitted two weeks ago (ref: ONB-2026-0058). This is "
                "blocking her from doing anything this morning — please "
                "treat as urgent."
            ),
            _a(
                "Apologies for the delay — checking ONB-2026-0058 now... "
                "found the issue: the ticket was submitted correctly, but the "
                "manager-approval step was routed to an old approver account "
                "that was deactivated in a role change last month, so it sat "
                "in an approval queue nobody was monitoring.\n\n"
                "I've manually approved and am provisioning her AD account, "
                "email, and CRM license right now — should be ready within "
                "the hour."
            ),
            _note(
                "Root cause: approval workflow for onboarding tickets still "
                "references the old org chart after last month's Customer "
                "Success reorg. Filed OPS-421 to have HR-Systems refresh the "
                "approver mapping rather than let this recur for the next "
                "wave of hires."
            ),
            _a(
                "Elena's AD account, email, and Salesforce CRM license are "
                "all active now — she should be able to log in immediately. "
                "Sent her the temporary password and MFA enrollment link "
                "directly. Really sorry again for the delay on day one."
            ),
            _c(
                "Confirmed she's logged in and working now. Thanks for the "
                "fast turnaround once you found it — please do fix that "
                "approval routing so the next hire doesn't hit the same wall."
            ),
        ),
    ),
    SeedScenario(
        title="Firewall rule change request for new partner API",
        category="Network",
        priority_hint="3 normal",
        articles=(
            _c(
                "We're integrating with a new logistics partner and need an "
                "outbound firewall rule allowing HTTPS (443) from our "
                "app-tier subnet (10.30.4.0/24) to their API endpoint at "
                "203.0.113.44. Change request form attached with the "
                "business justification and sign-off from our engineering "
                "lead."
            ),
            _a(
                "Reviewed the change request — looks complete. I've drafted "
                "the rule (allow tcp/443 from 10.30.4.0/24 to 203.0.113.44/32, "
                "logged) and scheduled it for tonight's standard change "
                "window (23:00) rather than applying immediately, per our "
                "change policy for anything touching the app-tier subnet."
            ),
            _note(
                "Rule added to firewall change batch CHG-1204. Peer-reviewed "
                "by network team, no conflicts with existing rules on that "
                "subnet. Will verify connectivity with a curl test to the "
                "partner endpoint right after the window closes."
            ),
            _a(
                "Change applied during tonight's window and verified — "
                "test connection from an app-tier host reached the partner "
                "endpoint successfully (TLS handshake completed, HTTP 200 on "
                "their health-check path). You should be clear to run your "
                "integration tests now."
            ),
            _c(
                "Confirmed on our end — the integration test suite passed "
                "against their API this morning. Thanks for scheduling this "
                "in properly rather than rushing it through."
            ),
        ),
    ),
    SeedScenario(
        title="Backup job failed overnight for finance file share",
        category="Software",
        priority_hint="4 high",
        articles=(
            _note(
                "Automated alert: nightly backup job 'finance-fileshare-daily' "
                "failed at 02:14 with error 'ERROR_SHARING_VIOLATION — file "
                "locked: Q4_Forecast_Master.xlsx'. Job aborted after 3 retry "
                "attempts. Opening ticket to track investigation and manual "
                "backup."
            ),
            _a(
                "Investigating the failed backup job for the finance file "
                "share. The failure is a sharing violation on "
                "Q4_Forecast_Master.xlsx — a file left open with exclusive "
                "lock by a workstation that appears to still be logged in "
                "overnight.\n\n"
                "Running a manual backup pass now, excluding that one locked "
                "file so the rest of the share is covered, and will retry the "
                "full job once the lock clears."
            ),
            _note(
                "Manual backup completed for all files except the locked "
                "spreadsheet. Traced the lock to a workstation left signed in "
                "over the weekend by a finance user; reached out to them "
                "directly to close the file rather than force-closing it "
                "remotely and risking unsaved changes."
            ),
            _a(
                "The file has since been closed and last night's full backup "
                "job completed successfully with no errors, all files "
                "including Q4_Forecast_Master.xlsx now covered. I've also "
                "added a pre-backup check that flags exclusively-locked files "
                "40 minutes before the job starts so we get earlier warning "
                "next time."
            ),
        ),
    ),
    SeedScenario(
        title="DNS resolution failing for internal wiki",
        category="Network",
        priority_hint="3 normal",
        articles=(
            _c(
                "Since this morning I can't reach wiki.internal.ourcompany"
                ".example from my machine — browser says "
                "'DNS_PROBE_FINISHED_NXDOMAIN'. Other internal sites (intranet, "
                "helpdesk) work fine. Colleagues on the same floor say it "
                "works for them."
            ),
            _a(
                "Thanks — since it's isolated to your machine rather than "
                "site-wide, this is likely a stale or corrupted local DNS "
                "cache rather than a server-side issue. Could you try "
                "flushing your DNS cache (ipconfig /flushdns on Windows) and "
                "retry?\n\n"
                "If that doesn't help, let me know and I'll check whether "
                "your machine is pointed at the correct internal DNS server "
                "in its network settings."
            ),
            _c(
                "Flushing the cache didn't fix it, still getting NXDOMAIN. "
                "Checked my network settings and DNS is set to 'automatic' — "
                "not sure what that resolves to though."
            ),
            _a(
                "Found it — your machine picked up the guest Wi-Fi's DNS "
                "server (8.8.8.8) instead of our internal resolver at some "
                "point, probably from briefly connecting to the guest "
                "network last week. Since wiki.internal only exists in our "
                "internal DNS zone, an external resolver will always return "
                "NXDOMAIN for it.\n\n"
                "I've corrected the DNS settings remotely via our management "
                "agent — should resolve now, could you confirm?"
            ),
            _c(
                "Confirmed, wiki loads fine now. Thanks for tracking down the "
                "actual cause instead of just telling me to reboot."
            ),
        ),
    ),
    SeedScenario(
        title="Disk space critical on file server FS-02",
        category="Hardware",
        priority_hint="5 very high",
        articles=(
            _note(
                "Automated alert: FS-02 (C:) at 96% capacity, 4.1 GB free. "
                "Threshold alert fired at 03:00. Escalating as high priority "
                "given the trend (was at 89% a week ago) — risk of the drive "
                "filling completely and taking down shared services on that "
                "host."
            ),
            _a(
                "Investigating FS-02 disk usage. Found the largest "
                "contributor: the SQL Server transaction log for the "
                "ticketing database has grown to 340 GB because log backups "
                "have been silently failing since a service account "
                "password rotation two weeks ago — the backup job's "
                "credentials were never updated.\n\n"
                "Taking a log backup now with corrected credentials to "
                "truncate the log, which should free the bulk of that space "
                "immediately."
            ),
            _note(
                "Log backup completed, transaction log truncated from 340 GB "
                "to 6 GB. Disk usage back down to 61%. Updated the backup "
                "job's service account credentials and added a monitor that "
                "alerts if log backups haven't succeeded in 24 hours, rather "
                "than relying on disk-space alerts as the only signal."
            ),
            _a(
                "Resolved — FS-02 is back to healthy disk levels (61% used, "
                "well under threshold) and log backups are running "
                "successfully again on their normal schedule. No data loss "
                "or service interruption occurred. We've also added earlier "
                "alerting so a similar credential-rotation miss surfaces "
                "within a day instead of two weeks."
            ),
        ),
    ),
    SeedScenario(
        title="Self-service password reset not sending SMS code",
        category="Access",
        priority_hint="3 normal",
        articles=(
            _c(
                "I'm locked out of my account and trying to use the "
                "self-service password reset, but the SMS verification code "
                "never arrives on my phone. I've requested it four times over "
                "the last 20 minutes. My phone number in the system should be "
                "correct, it's the one I always use."
            ),
            _a(
                "Thanks for the details. Checked our SMS gateway logs — I can "
                "see the send attempts, and they're failing with "
                "'ERR_CARRIER_FILTERED', which usually means the carrier is "
                "blocking messages from our short code, sometimes because of "
                "message content or the recipient having previously reported "
                "similar messages as spam.\n\n"
                "As a workaround, I can trigger an email-based reset code to "
                "your registered address instead — would that work for you "
                "right now?"
            ),
            _c(
                "Yes please, email works for me. Just received it and was "
                "able to reset my password successfully."
            ),
            _note(
                "SMS carrier filtering issue affects at least this one "
                "carrier (confirmed via gateway provider's status page — "
                "known ongoing issue on their end, no ETA given). Flagging "
                "for the identity team to consider making email reset the "
                "default rather than a manual fallback until the carrier "
                "issue clears."
            ),
        ),
    ),
    SeedScenario(
        title="SSO login loop after identity provider migration",
        category="Access",
        priority_hint="4 high",
        articles=(
            _c(
                "Since this morning's IdP migration, logging into the "
                "internal portal just bounces me back to the login page "
                "after I authenticate — no error message, it just loops. "
                "Tried three different browsers and it's the same every "
                "time. Several colleagues report the same thing."
            ),
            _a(
                "Thanks, this matches reports we're getting from a few other "
                "teams too — escalating to high priority. Initial look at the "
                "portal's SSO logs shows it's still configured to trust the "
                "old IdP's signing certificate, which was rotated as part of "
                "this morning's migration, so every otherwise-valid "
                "assertion is being rejected silently.\n\n"
                "Updating the trusted certificate now — should take about 15 "
                "minutes to propagate."
            ),
            _note(
                "Confirmed: migration runbook step 'update SP-side signing "
                "cert trust for all relying-party apps' only covered the "
                "primary 6 apps in the tracked list; the internal portal was "
                "onboarded to SSO after that list was last updated and got "
                "missed. Auditing all relying-party apps against the current "
                "IdP metadata to catch any other stragglers."
            ),
            _a(
                "Certificate trust updated and propagated — logins are "
                "working normally again, confirmed with a fresh login myself "
                "just now. We found and are also proactively re-checking two "
                "other apps that had a similar gap; will follow up "
                "separately if either of those needs a fix."
            ),
            _c("Can confirm I'm in now without any looping. Thanks for the fast root-causing."),
        ),
    ),
    SeedScenario(
        title="Phishing email reported — suspicious invoice attachment",
        category="Software",
        priority_hint="5 very high",
        articles=(
            _c(
                "Forwarding a suspicious email I received titled 'Overdue "
                "Invoice #88213 — Action Required' from an address I don't "
                "recognize (billing-support@invoice-alerts.example), with a "
                "ZIP attachment. Didn't open the attachment, just wanted "
                "security to take a look since it looked off — wrong logo, "
                "urgent tone, and the sender domain doesn't match any vendor "
                "we use."
            ),
            _note(
                "Good catch, not opened, thank you. Escalating as very high "
                "priority per phishing procedure. Submitting the attachment "
                "to sandbox analysis and checking mail logs for other "
                "recipients of the same campaign."
            ),
            _a(
                "Confirmed this is a phishing attempt — the ZIP contains an "
                "obfuscated script that would have attempted to download a "
                "second-stage payload if run. We found 11 other recipients "
                "of the same email across the company; none reported opening "
                "the attachment, and we've blocked the sender domain and "
                "the file hash at the mail gateway.\n\n"
                "Thanks again for reporting instead of opening it — this is "
                "exactly the right call. No action needed on your end."
            ),
            _note(
                "Added sender domain and attachment SHA-256 to the mail "
                "gateway blocklist. Notified the 11 other recipients "
                "directly with a reminder of the reporting process. No "
                "evidence of any successful compromise across the "
                "environment."
            ),
        ),
    ),
    SeedScenario(
        title="Second monitor not detected after docking station update",
        category="Hardware",
        priority_hint="2 low",
        articles=(
            _c(
                "After the docking station firmware update that was pushed "
                "last night, my second monitor isn't being detected anymore "
                "when I dock my laptop. First monitor and everything else "
                "(network, USB peripherals) works fine through the dock."
            ),
            _a(
                "Thanks for reporting — we've had one other similar report "
                "after last night's dock firmware push. Could you try "
                "unplugging the second monitor's cable from the dock and "
                "reconnecting it, ideally into a different video port on the "
                "dock if it has more than one? Sometimes the firmware update "
                "resets port negotiation and a fresh connection clears it."
            ),
            _c(
                "That did it — moved the cable to the other DisplayPort on "
                "the dock and the second monitor came right up. Thanks!"
            ),
            _note(
                "Two reports now of the same symptom after the dock firmware "
                "push, both resolved by reseating the cable on a different "
                "port. Monitoring for further reports before deciding whether "
                "this needs a vendor bug report or just a heads-up email to "
                "everyone with this dock model."
            ),
        ),
    ),
    SeedScenario(
        title="MFA reset needed — lost phone with authenticator app",
        category="Access",
        priority_hint="4 high",
        articles=(
            _c(
                "I lost my phone over the weekend and it had my authenticator "
                "app on it — I'm now locked out of everything that needs MFA. "
                "I've already reported the phone as lost/stolen to our "
                "carrier and wiped it remotely via Find My Device. Can "
                "someone reset my MFA enrollment so I can set it up on my new "
                "phone?"
            ),
            _a(
                "Sorry to hear about the phone — good call on the remote wipe "
                "already. Before resetting MFA I need to verify your identity "
                "through an out-of-band channel per policy: could you join a "
                "quick video call with your camera on and your employee badge "
                "visible, or have your manager confirm the request in "
                "writing? Either works."
            ),
            _c(
                "Just had a quick call with Alex from IT who confirmed my "
                "identity via video and badge, thanks for being careful about "
                "this given the circumstances."
            ),
            _note(
                "Identity verified via video call + employee badge per MFA "
                "reset policy, confirmed by Alex Turner. Old MFA enrollment "
                "revoked, new enrollment link sent to the user's registered "
                "corporate email (not the lost device)."
            ),
            _a(
                "All set — your old authenticator enrollment has been "
                "revoked and I've sent a new enrollment QR code to your "
                "corporate email. You'll need to complete enrollment on the "
                "new phone before your next login. Let us know once it's "
                "done and we'll do a final check."
            ),
            _c(
                "Enrolled on the new phone and logged in successfully. Thanks "
                "for handling this carefully and quickly."
            ),
        ),
    ),
    SeedScenario(
        title="Software install request — statistical analysis package",
        category="Software",
        priority_hint="2 low",
        articles=(
            _c(
                "Could IT install R and RStudio on my work laptop? Our data "
                "team standardized on them for the Q3 analysis project and I "
                "don't have local admin rights to install it myself."
            ),
            _a(
                "Sure — R (4.4.1) and RStudio Desktop have been added to your "
                "Company Portal app catalog. You should be able to install "
                "both yourself from there without needing admin rights, "
                "typically ready within about 10 minutes.\n\n"
                "If either fails to install or you hit a permissions error "
                "during install, let us know and we'll push it directly to "
                "your machine instead."
            ),
            _c("Both installed cleanly from the portal, thank you — no issues."),
        ),
    ),
    SeedScenario(
        title="Intermittent network outage in the third-floor east wing",
        category="Network",
        priority_hint="4 high",
        articles=(
            _c(
                "Several of us in the east wing on the third floor have had "
                "our network connection drop intermittently all afternoon — "
                "roughly every 20-30 minutes for a minute or two at a time. "
                "Both wired and Wi-Fi seem affected. Rest of the building "
                "seems fine based on what people on other floors are saying."
            ),
            _a(
                "Thanks, escalating given the pattern and scope. Checking the "
                "switch stack serving that wing (SW-3E-01/02) now — seeing "
                "repeated STP topology-change events roughly matching your "
                "timing, which points to a flapping uplink rather than a "
                "config issue."
            ),
            _note(
                "Confirmed: uplink port Gi1/0/24 on SW-3E-01 is flapping — "
                "link up/down every ~25 minutes, correlating with a "
                "temperature spike logged on that switch (comms closet AC "
                "unit appears to be underperforming this afternoon). "
                "Notified facilities about the AC; temporarily failing the "
                "uplink over to the redundant path on SW-3E-02 as a "
                "workaround."
            ),
            _a(
                "Update: failed the uplink over to the redundant switch path "
                "as a temporary fix — connectivity in the east wing has been "
                "stable for the last 45 minutes with no further drops. Root "
                "cause looks like an overheating comms closet on that floor; "
                "facilities is sending someone to check the AC unit now. "
                "We'll keep the redundant path active until that's resolved."
            ),
            _c(
                "Can confirm things have been stable on our end too since "
                "your last update. Thanks for the quick diagnosis."
            ),
        ),
    ),
    SeedScenario(
        title="TLS certificate renewal needed for partner API gateway",
        category="Network",
        priority_hint="4 high",
        articles=(
            _note(
                "Automated alert: TLS certificate for api-gateway.ourcompany"
                ".example expires in 5 days (2026-07-30). Opening ticket to "
                "track renewal — this endpoint serves the partner API "
                "integration, so an expired cert would break external partner "
                "calls, not just show a browser warning."
            ),
            _a(
                "Starting the renewal for api-gateway.ourcompany.example. "
                "Generated a new CSR and submitted it to our CA; approval "
                "usually takes 1-2 business days for this certificate type. "
                "Will deploy to the gateway and verify the full chain once "
                "issued, well ahead of the expiry date."
            ),
            _note(
                "New certificate issued by CA, deployed to both gateway nodes "
                "(active/standby) and verified with openssl s_client against "
                "each — chain validates correctly, new expiry is "
                "2027-07-30. Also confirmed the automated renewal job "
                "(certbot-style, 30-day-before trigger) is correctly "
                "configured this time, since last year's near-miss was "
                "caused by that job silently failing."
            ),
            _a(
                "Certificate renewed and verified on both gateway nodes well "
                "ahead of the expiry date — no action needed from partners, "
                "no downtime expected. We've also double-checked that the "
                "automated renewal job is healthy so this shouldn't need "
                "manual intervention again next year."
            ),
        ),
    ),
)
