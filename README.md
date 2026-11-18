# Sleep-Aware Alarm Scheduler

A fault-tolerant scheduler that reads wearable sleep telemetry, replans in near real time, and guarantees the alarm fires even when upstream data or APIs fail.

# Smart sleep alarm

Watches your sleep overnight (Google Health API), picks the best moment to wake
you inside a window you choose, then fires an Alexa routine on your Echo.

```
Watch/phone -> Google Health API -> this app (plans every ~10 min) -> Voice Monkey -> Alexa Routine -> Echo alarm
```

## Two things worth knowing first

1. **Google Fit's API is being retired.** Its replacement for cloud access is the
   **Google Health API** (v4, `health.googleapis.com`), which is what this app uses.
   It reads sleep from your Google Health / Fitbit account, so you need a watch or
   tracker that syncs there. Data from the old Fit REST API does not carry over automatically.
2. **Alexa has no public "set alarm" API.** So this app is the alarm clock: it works out
   the wake time and, at that moment, triggers an Alexa Routine through
   [Voice Monkey](https://voicemonkey.io). The routine can play an alarm sound or music,
   raise the volume, turn on lights, etc.

## Set it up

### 1. Google
1. In Google Cloud Console, create a project and enable the **Google Health API**.
2. Configure the OAuth consent screen and add the scope
   `https://www.googleapis.com/auth/googlehealth.sleep.readonly`.
3. Create an **OAuth client ID** (type: Web application) with redirect URI
   `http://localhost:8000/auth/callback`.
4. Put the client ID and secret in `.env`.

Note: while the consent screen is in "Testing", Google expires refresh tokens after
7 days. Publish the app (it's just for you) to avoid reconnecting weekly.

### 2. Alexa (Voice Monkey)
1. Create a Voice Monkey account and enable the Voice Monkey skill in the Alexa app.
2. Add a **Routine Trigger** device. Copy your API token and the device ID into `.env`.
3. In the Alexa app, make a routine: **When** Smart Home > your Voice Monkey device,
   **Action** whatever should wake you (alarm sound / music, volume, lights),
   **Device** your Echo.

### 3. Run
```bash
cp .env.example .env        # fill it in
pip install -r requirements.txt
python -m pytest            # optional: planner tests
uvicorn app.main:app --host 127.0.0.1 --port 8000
```
Or with Docker:
```bash
docker build -t sleep-alarm .
docker run -d --restart unless-stopped -p 127.0.0.1:8000:8000 --env-file .env -v "$PWD/data:/srv/data" sleep-alarm
```
Open http://localhost:8000 (any username, the `ADMIN_PASSWORD` as password), click
**Connect** for Google, then **Test the alarm now**.

It must run all night, so use something always on (Raspberry Pi, home server, small VPS).
On a remote machine, keep it bound to localhost and reach it with
`ssh -L 8000:localhost:8000 you@server`, so the localhost redirect URI still works
and the password isn't sent over plain HTTP.

## How the wake time is chosen
- You set an earliest and latest wake time and a target sleep length.
- The app finds your sleep onset, estimates your cycle length (from your deep-sleep
  bouts when available, otherwise 90 min), and picks the time in the window closest to a
  cycle boundary while keeping you near the target.
- Within 30 min of the alarm, with sleep-stage data under 20 min old: deep sleep pushes
  the alarm 15 min later (never past your latest time); light sleep after hitting your
  target pulls it forward.
- Before any data arrives it plans from your usual bedtime.

## Safety rails
- The **latest wake time is a hard deadline.** It fires then even if Google is down, the
  token expired or no data ever came in.
- If Alexa fails it retries every 30 s for 10 min.
- **Keep a normal backup alarm** (phone or a plain Alexa alarm) a few minutes after your
  latest time. If this server, your Wi-Fi or Voice Monkey is down, that's what wakes you.

## First night: check the data
Google's sleep list can only be filtered by session *end* time, so a session that's still
in progress may not appear until your watch syncs. After the first night open
`/api/debug/sleep` (also linked in the dashboard) to see what Google returned and how it
was parsed. If sessions only show up after you wake, the app is using your usual bedtime
all night and the alarm lands at a cycle boundary in your window; it's still safe, just
less adaptive. Stage field names are parsed defensively; if the parsed output looks wrong,
that endpoint is where to fix `parse_snapshot` in `app/google_health.py`.

## Files
```
app/planner.py        wake-time logic (pure, tested)
app/google_health.py  OAuth + sleep reads
app/alexa.py          Voice Monkey trigger
app/scheduler.py      overnight loop + failsafe
app/main.py           dashboard + API
app/static/index.html dashboard
tests/                planner tests
```
Tokens are stored in plain SQLite at `DB_PATH`; keep that file private.
