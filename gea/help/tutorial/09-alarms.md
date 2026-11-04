# 9. Alarms, notifications and approvals
Who: office
Where: #/alarms
Takes: 20 minutes
> Setpoints that fire on every refresh, the event log, where the messages go, and the two signatures a well test needs.

## Steps
1. Open **Alarms**. The definitions come from `alarm_definitions` in the configuration store: each with its tag, setpoint, deadband, delay and priority. **Derive defaults from the first historian well** writes the limits the gauge's datasheet gives; edit them and **Validate and commit** - every version is kept and named.
2. Alarms run at every refresh. An active alarm is listed with its value and when it fired; the Alarms tab carries a badge with those not yet acknowledged.
3. **Acknowledge** one when you have seen it; **Shelve** one you are working on, with the hours and a reason; **Unshelve** when done. Each action is an audit line with your name.
4. **Program alerts** on the same tab are the conditions nobody watches by hand: an earthquake near a well at a protocol level, the earthquake watch gone quiet, a motor's service interval used up, a penetrator check outside its rating, a signed dossier the workspace no longer matches, a certificate about to expire.
5. **Notifications**: Administration, **Notifications**, **Load the example**. A channel is a webhook or a mailbox (the password by an environment variable, never in the file); a rule routes an event to a channel - `alarm.activated` by priority, `patch.down`, `job.failed`, `approval.pending`, `seismic.protocol`, `pad.summary`. **Validate and commit**, then **Send a test** per channel; every attempt is in the delivery log.
6. **The month**: the monthly note, written at refresh with `--month`, says for each alarm MET or NOT MEASURED - never MET when it cannot say so.
7. **Approvals**: a well test waits here for two signatures. An approver reads the test and presses **Record decision** - approve, or reject with a reason; a second approver does the same. The Approvals tab carries a badge with what is waiting.

## You know it worked when
The test message arrives on your channel, and an alarm you acknowledge leaves the badge.

Help: alarms, notifications, alerts, well-tests, quality
