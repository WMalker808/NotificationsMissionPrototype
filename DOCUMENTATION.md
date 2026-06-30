# Dispatch — Newsletter Hub: Full Technical Documentation

## Overview

Dispatch is a single-page internal web tool for sending breaking news newsletters via the Braze customer engagement platform. A user pastes a Guardian article URL, the app fetches the headline and preview text automatically, the user chooses a delivery timing mode, and submits. The Flask backend then calls the Braze REST API to send or schedule the campaign.

---

## Tech Stack

| Layer | Technology |
|---|---|
| Backend | Python 3.11, Flask |
| Frontend | Vanilla HTML/CSS/JS (no framework) |
| Email delivery | Braze REST API |
| Deployment | Render (gunicorn WSGI server) |
| Dependencies | `flask`, `requests`, `python-dotenv`, `gunicorn` |

---

## Project Structure

```
dispatch/
├── app.py                  # Flask application — all backend logic
├── requirements.txt        # Python dependencies
├── render.yaml             # Render.com deployment config
├── .env                    # Environment variables (not committed)
├── static/
│   └── guardian.css        # Design token stylesheet
└── templates/
    └── index.html          # Single-page UI (HTML + embedded CSS + JS)
```

---

## Environment Variables

All secrets and configuration are loaded from a `.env` file via `python-dotenv`. The following variables are required:

| Variable | Description |
|---|---|
| `BRAZE_API_KEY` | Braze REST API key. Must have permissions for `campaigns.trigger.send` and `campaigns.trigger.schedule.create`. |
| `BRAZE_REST_ENDPOINT` | Base URL of the Braze REST endpoint (e.g. `https://rest.fra-01.braze.eu`). Defaults to the EU-01 endpoint if not set. |
| `BRAZE_CAMPAIGN_ID` | The ID of the Braze campaign to send. All sends — regardless of timing mode — go to this campaign and only this campaign. |
| `SECRET_KEY` | Flask session secret. If not set, a random key is generated at startup (sessions will not persist across restarts). |
| `PORT` | Port for the development server. Defaults to `5050`. |

---

## Backend (`app.py`)

### Application Startup

```python
load_dotenv()
app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", secrets.token_hex(32))
```

Environment variables are loaded and a Flask secret key is set for session management.

---

### CSRF Protection

All API endpoints call `_require_csrf()` before processing any request.

**Mechanism:**
1. On page load, the server generates a `secrets.token_hex(32)` token, stores it in the user's server-side session, and embeds it in the HTML as a `<meta name="csrf-token">` tag.
2. Every JavaScript `fetch()` call sends the token back in the `X-CSRF-Token` request header.
3. `_require_csrf()` compares the header value against `session["csrf_token"]`. If they do not match, the endpoint returns `403`.

```python
def _require_csrf():
    token = request.headers.get("X-CSRF-Token")
    if not token or token != session.get("csrf_token"):
        return jsonify({"error": "Invalid request"}), 403
    return None
```

---

### Braze Integration

The app integrates with Braze using two internal functions. Both always read `BRAZE_API_KEY`, `BRAZE_REST_ENDPOINT`, and `BRAZE_CAMPAIGN_ID` exclusively from environment variables. There is no mechanism for any user-supplied value to override the campaign ID.

#### `_braze_send(headline, body, url, image_url=None)`

Triggers an **immediate** send via `POST /campaigns/trigger/send`.

**Braze payload:**
```json
{
  "campaign_id": "<BRAZE_CAMPAIGN_ID>",
  "broadcast": true,
  "trigger_properties": {
    "headline": "<headline>",
    "subject": "Breaking news: <headline>",
    "body": "<body>",
    "url": "<url>",
    "image_url": "<image_url or empty string>"
  }
}
```

`broadcast: true` sends to all users subscribed to the campaign without requiring a list of recipient IDs.

#### `_braze_schedule(headline, body, url, schedule_time, at_optimal_time=False, image_url=None)`

Schedules a send via `POST /campaigns/trigger/schedule/create`.

**Braze payload:**
```json
{
  "campaign_id": "<BRAZE_CAMPAIGN_ID>",
  "broadcast": true,
  "trigger_properties": { "...same as above..." },
  "schedule": {
    "time": "<ISO 8601 UTC datetime>",
    "at_optimal_time": true  // only present when at_optimal_time=True
  }
}
```

- `schedule.time` must be an ISO 8601 UTC datetime string that is **not in the past**.
- `schedule.at_optimal_time` activates Braze Intelligent Timing when `True`.

Both functions raise an exception (which propagates as a 500 response) if the Braze API returns a non-2xx status.

---

### Article Fetching — `_MetaParser`

A lightweight HTML parser (subclass of `html.parser.HTMLParser`) that extracts Open Graph metadata from Guardian article pages.

**Extracts:**
- `og:title` → used as the headline (falls back to `<title>` tag)
- `og:description` → used as the body/preview text
- `og:image` → used as the image URL

After extraction, known Guardian title suffixes (` | The Guardian`, ` - The Guardian`) are stripped from the headline.

---

### Routes

#### `GET /`

Renders `index.html`. Generates a fresh CSRF token for the session on every page load.

```python
session["csrf_token"] = secrets.token_hex(32)
return render_template("index.html", csrf_token=session["csrf_token"])
```

---

#### `POST /api/fetch-article`

Fetches a Guardian article URL and returns its headline, body, and image URL.

**Request body:**
```json
{ "url": "https://www.theguardian.com/..." }
```

**Validation:**
- CSRF token must be valid.
- URL scheme must be `https`.
- URL host must be exactly `www.theguardian.com`. No other domains are accepted.

**Process:**
1. Makes an HTTP GET to the article URL with a realistic browser User-Agent and `Accept-Language: en-GB`.
2. Feeds the HTML response through `_MetaParser`.
3. Strips Guardian suffixes from the headline.
4. Returns `{ "headline": "...", "body": "...", "image_url": "..." }`.

**Error response:** `{ "error": "<message>" }` with HTTP 400.

---

#### `POST /api/send`

Triggers a newsletter send via Braze.

**Request body:**
```json
{
  "headline": "Article headline",
  "body": "Preview text",
  "url": "https://www.theguardian.com/...",
  "image_url": "https://...",
  "timing": "immediate" | "scheduled" | "intelligent",
  "sched_at": "2026-05-15T13:30:00"  // required only when timing=scheduled
}
```

**Timing mode behaviour:**

| Mode | Backend action |
|---|---|
| `immediate` | Calls `_braze_send()` — sends right now |
| `scheduled` | Calls `_braze_schedule()` with `sched_at` as the time. Returns 400 if `sched_at` is missing. `sched_at` is a bare UTC ISO-8601 string (`YYYY-MM-DDTHH:MM:SS`) converted from the user's local date/time by the browser. |
| `intelligent` | Calls `_braze_schedule()` with the current UTC time as `schedule.time` and `at_optimal_time=True`. Braze then delivers to each subscriber at their individually optimal time within the next 24 hours. |

**Dev mode bypass:** If `BRAZE_API_KEY` is not set, the endpoint returns `{ "success": true }` immediately without calling Braze. This allows local development without credentials.

**Success response:** `{ "success": true }`
**Error response:** `{ "success": false, "error": "<message>" }` with HTTP 400 or 500.

---

## Frontend (`templates/index.html`)

### Layout

The UI is a fixed-height single-page app with three structural zones:

```
┌──────────┬────────────────────────────┬──────────────┐
│  Sidebar │  Form Panel (scrollable)   │ Preview Panel│
│  (60px)  │                            │  (300px)     │
└──────────┴────────────────────────────┴──────────────┘
```

The preview panel is hidden on viewports narrower than 900px.

---

### Design System (`static/guardian.css`)

A CSS custom property stylesheet providing Guardian-branded design tokens:

- **Colours:** `--guardian-blue` (`#052962`), `--guardian-blue-bright` (`#1556b0`), `--guardian-news-red` (`#c70000`), ink scale (`--ink-900` through `--ink-50`).
- **Typography:** `--font-sans` (Guardian Sans fallback chain), `--font-serif` (Guardian Egyptian / Source Serif 4 fallback chain). Source Serif 4 is loaded from Google Fonts.
- **Spacing/radius/shadow tokens** for consistent component styling.

---

### Form Sections

#### Content Card

- **Guardian article URL field** — freetext input. Triggers `onUrlInput()` on each keystroke.
- **Subject line** — text input, max 100 characters. Character counter turns amber at 90%, purple at 100%.
- **Preview text / body summary** — textarea, max 280 characters. Same counter behaviour.

#### Channel Card

Displays a fixed informational panel showing the delivery channel: "Newsletter — Email via Braze". No user interaction; the channel is not configurable.

#### Audience Segments Card

Displays five segment toggle buttons: United Kingdom, United States, Australia, Europe, Global (ALL).

> **Important:** Segment selection is UI-only and not wired to the backend. The warning card in the UI makes this explicit. All sends go to the same Braze campaign regardless of which segments are selected. Segment targeting is marked as "Not in production".

The segment state is tracked in `state.segments` (a plain object keyed by segment ID). `hasSegment` in `updateSendButton()` is hardcoded to `true`, meaning the send button does not actually require a segment to be selected.

#### Delivery & Timing Card

Three mutually exclusive timing mode buttons. Selecting a mode:
1. Updates visual state (border colour, background).
2. Shows/hides the relevant sub-panel (date/time picker for scheduled, informational callout for intelligent).
3. Updates the routing detail text in the preview panel.
4. Re-evaluates the send button state.

**Scheduled sub-panel:** Shows a date input and a time input (both in the user's local time). On send, these are combined and converted to UTC ISO-8601 via `new Date(...).toISOString().slice(0, 19)`.

**Intelligent timing callout:** Static informational panel explaining Braze Intelligent Timing behaviour. No user inputs.

---

### JavaScript State

```javascript
const state = {
  segments: {},   // { uk: true, us: false, ... }
  timing: 'immediate' | 'scheduled' | 'intelligent'
};
```

---

### Key JavaScript Functions

| Function | Purpose |
|---|---|
| `init()` | Builds segment buttons dynamically from the `SEGMENTS` array and calls `updateSendButton()`. |
| `onUrlInput()` | Debounces URL input by 500ms then calls `fetchArticle()`. |
| `fetchArticle(url)` | POSTs to `/api/fetch-article`, populates headline and body fields on success, shows status message. |
| `toggleSegment(id)` | Toggles a segment in `state.segments`, updates button appearance, hint text, preview pills, and send button. |
| `selectTiming(t)` | Sets `state.timing`, updates timing button classes, shows/hides sub-panels, updates routing detail. |
| `updatePreview()` | Syncs live email preview with current headline and body values. |
| `updateCharCount(inputId, countId, max)` | Updates character count display and applies warning/over CSS classes. |
| `updateSendButton()` | Enables/disables send button based on: headline non-empty, and (if scheduled) date+time filled. |
| `sendAlert()` | POSTs to `/api/send` with all form values. Shows success banner on success, `alert()` on failure. Resets button after 3 seconds. |
| `dismissSuccess()` | Hides the success banner. |

---

### CSRF on the Frontend

The CSRF token is read from the `<meta name="csrf-token">` tag on page load:

```javascript
const CSRF = document.querySelector('meta[name="csrf-token"]').content;
```

Every `fetch()` call includes it as a header:

```javascript
headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': CSRF }
```

---

### Email Preview Panel

The right-hand panel renders a live miniature email mockup:
- Header bar: navy background, "D" logo, "The Guardian" brand name.
- Headline: updates in real time as the user types, prefixed with "Breaking news: ".
- Body: updates in real time.
- CTA button: static "Read more →" link (not functional in preview).

Below the email mockup, the panel shows:
- **Routing:** channel name and timing detail (updates when timing mode changes).
- **Sending to:** pills for each selected segment.
- **Send button:** state-aware (disabled/ready/sent), label changes per timing mode.

---

### Success Banner

A green banner shown after a successful send. Content varies by timing mode:

| Mode | Banner title |
|---|---|
| immediate | "Breaking news email sent" |
| scheduled | "Breaking news email scheduled for `<date> <time>` (local)" |
| intelligent | "Breaking news email queued for intelligent delivery" |

Dismissed by clicking the × button.

---

## Braze Campaign Requirements

The Braze campaign referenced by `BRAZE_CAMPAIGN_ID` must be configured as follows:

- **Delivery type:** API-Triggered Delivery (not scheduled, not action-based).
- **Audience:** set to broadcast or the intended subscriber list.
- **Trigger properties:** the campaign template must reference the following Liquid variables passed via `trigger_properties`:
  - `{{trigger_properties.headline}}`
  - `{{trigger_properties.subject}}`
  - `{{trigger_properties.body}}`
  - `{{trigger_properties.url}}`
  - `{{trigger_properties.image_url}}`

The Braze API key must have the following endpoint permissions enabled:
- `campaigns.trigger.send`
- `campaigns.trigger.schedule.create`

---

## Deployment (Render)

Defined in `render.yaml`:

```yaml
services:
  - type: web
    name: dispatch
    env: python
    buildCommand: pip install -r requirements.txt
    startCommand: gunicorn app:app
    envVars:
      - key: PYTHON_VERSION
        value: 3.11.0
```

Environment variables (`BRAZE_API_KEY`, `BRAZE_CAMPAIGN_ID`, `BRAZE_REST_ENDPOINT`, `SECRET_KEY`) must be set in the Render dashboard under the service's environment settings. They are not committed to the repository.

---

## Local Development

```bash
# Install dependencies
pip install -r requirements.txt

# Create .env file with required variables (see Environment Variables section)

# Start development server (defaults to port 5050)
python app.py
```

If `BRAZE_API_KEY` is absent from `.env`, sends will return success without hitting Braze, allowing safe UI development.

---

## Security Notes

- **CSRF protection** is applied to all state-changing endpoints.
- **URL allowlist:** article fetching is restricted to `https://www.theguardian.com` only. No other domains can be fetched.
- **Campaign ID is not user-controllable.** It is read exclusively from the environment. There is no API parameter that can redirect sends to a different campaign.
- **Secrets** are never embedded in the frontend or committed to source control.
