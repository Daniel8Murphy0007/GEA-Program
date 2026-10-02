# Notifications

> Rules route events to a webhook or a mailbox; every attempt is logged; secrets stay out of the file.

**The command**: Administration -> Notifications: "Load the example", edit, "Validate and commit" (it is versioned as `notifications`), "Send a test" per channel. `gea notify --example`, `gea notify --workspace C:\site --test mail`, `gea notify --workspace C:\site --log 20`. Events: alarm.activated (by priority), alarm.shelved, job.failed, patch.down, patch.up, approval.pending, file.imported.

**What it writes**: `records/notifications.jsonl` - one line per delivery attempt and per suppression (the quiet window stops repeats of the same key); the service's poller reads the alarm logs, the jobs, the patch states and the approval queue every 10 seconds and starts from a baseline at start-up, so history is never re-announced.

**The number to check**: the "Last deliveries" table: `sent`, `suppressed` or `failed` with the reason (a 404 from the webhook, a refused SMTP login).

**What this page will not call a measurement**: a notification is a copy of an event, not the event; the alarm log and the audit log are the record. A configuration that contains a password is declined - an SMTP password comes from the environment variable the channel names.

Related: `gea help alarms`, `gea help patches`.
