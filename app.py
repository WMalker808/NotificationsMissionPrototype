from flask import Flask, render_template, jsonify, request, session
from html.parser import HTMLParser
from html import unescape
from urllib.parse import urlparse
from datetime import datetime, timezone
from dotenv import load_dotenv
import requests as http
import secrets
import os

load_dotenv()

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", secrets.token_hex(32))

_GUARDIAN_HOST = "www.theguardian.com"


def _require_csrf():
    token = request.headers.get("X-CSRF-Token")
    if not token or token != session.get("csrf_token"):
        return jsonify({"error": "Invalid request"}), 403
    return None


def _braze_send(headline, body, url, image_url=None):
    api_key = os.environ.get("BRAZE_API_KEY", "")
    endpoint = os.environ.get("BRAZE_REST_ENDPOINT", "https://rest.fra-01.braze.eu")
    campaign_id = os.environ.get("BRAZE_CAMPAIGN_ID", "")

    if not api_key:
        raise ValueError("BRAZE_API_KEY not set")
    if not campaign_id:
        raise ValueError("BRAZE_CAMPAIGN_ID not set")

    payload = {
        "campaign_id": campaign_id,
        "broadcast": True,
        "trigger_properties": {
            "headline": headline,
            "subject": f"Breaking news: {headline}",
            "body": body,
            "url": url,
            "image_url": image_url or "",
        },
    }

    resp = http.post(
        f"{endpoint}/campaigns/trigger/send",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json=payload,
        timeout=15,
    )
    if not resp.ok:
        print("Braze error:", resp.status_code, resp.text)
    resp.raise_for_status()
    return resp.json()


def _braze_schedule(headline, body, url, schedule_time, at_optimal_time=False, image_url=None):
    api_key = os.environ.get("BRAZE_API_KEY", "")
    endpoint = os.environ.get("BRAZE_REST_ENDPOINT", "https://rest.fra-01.braze.eu")
    campaign_id = os.environ.get("BRAZE_CAMPAIGN_ID", "")

    if not api_key:
        raise ValueError("BRAZE_API_KEY not set")
    if not campaign_id:
        raise ValueError("BRAZE_CAMPAIGN_ID not set")

    schedule = {"time": schedule_time}
    if at_optimal_time:
        schedule["at_optimal_time"] = True

    payload = {
        "campaign_id": campaign_id,
        "broadcast": True,
        "trigger_properties": {
            "headline": headline,
            "subject": f"Breaking news: {headline}",
            "body": body,
            "url": url,
            "image_url": image_url or "",
        },
        "schedule": schedule,
    }

    resp = http.post(
        f"{endpoint}/campaigns/trigger/schedule/create",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json=payload,
        timeout=15,
    )
    if not resp.ok:
        print("Braze error:", resp.status_code, resp.text)
    resp.raise_for_status()
    return resp.json()


class _MetaParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.og_title = None
        self.og_description = None
        self.og_image = None
        self._title_buf = []
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "meta":
            prop = attrs.get("property") or attrs.get("name") or ""
            content = attrs.get("content", "")
            if prop == "og:title" and not self.og_title:
                self.og_title = content
            elif prop == "og:description" and not self.og_description:
                self.og_description = content
            elif prop == "og:image" and not self.og_image:
                self.og_image = content
        elif tag == "title":
            self._in_title = True

    def handle_data(self, data):
        if self._in_title:
            self._title_buf.append(data)

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False

    @property
    def title(self):
        return "".join(self._title_buf).strip()


@app.route("/api/fetch-article", methods=["POST"])
def fetch_article():
    err = _require_csrf()
    if err:
        return err

    url = (request.json or {}).get("url", "").strip()
    if not url:
        return jsonify({"error": "No URL provided"}), 400

    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.netloc != _GUARDIAN_HOST:
        return jsonify({"error": "Only Guardian article URLs are supported"}), 400

    try:
        resp = http.get(url, timeout=8, headers={
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "en-GB,en;q=0.9",
        })
        resp.raise_for_status()
        parser = _MetaParser()
        parser.feed(resp.text)
        headline = unescape(parser.og_title or parser.title or "")
        for suffix in (" | The Guardian", " - The Guardian"):
            if suffix in headline:
                headline = headline.split(suffix)[0].strip()
                break
        body = unescape(parser.og_description or "")
        image_url = parser.og_image or ""
        return jsonify({"headline": headline, "body": body, "image_url": image_url})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400


@app.route("/")
def index():
    session["csrf_token"] = secrets.token_hex(32)
    return render_template("index.html", csrf_token=session["csrf_token"])


@app.route("/api/send", methods=["POST"])
def send_alert():
    err = _require_csrf()
    if err:
        return err

    data = request.json or {}
    headline = data.get("headline", "")
    body = data.get("body", "")
    url = data.get("url", "")
    image_url = data.get("image_url", "")
    timing = data.get("timing", "immediate")
    sched_at = data.get("sched_at")  # bare UTC ISO-8601, e.g. "2026-05-15T13:30:00"

    if not os.environ.get("BRAZE_API_KEY"):
        return jsonify({"success": True})

    try:
        if timing == "immediate":
            _braze_send(headline, body, url, image_url)
        elif timing == "scheduled":
            if not sched_at:
                return jsonify({"success": False, "error": "Schedule time is required"}), 400
            _braze_schedule(headline, body, url, sched_at, image_url=image_url)
        elif timing == "intelligent":
            today = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
            _braze_schedule(headline, body, url, today, at_optimal_time=True, image_url=image_url)
        else:
            return jsonify({"success": False, "error": "Unknown timing mode"}), 400
    except Exception as exc:
        return jsonify({"success": False, "error": str(exc)}), 500

    return jsonify({"success": True})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5050))
    app.run(host="0.0.0.0", port=port)
