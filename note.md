class UpstreamError(Exception):
  ...


class RateLimitError(UpstreamError):
  ...


# ── id-service ────────────────────────────────────────────────
# Resolves ONE external identifier per call into an internal user_id.
# A customer's accounts get merged, so a single person may have SEVERAL user_ids:
# the full set = user_id + all merged_ids.
# Not found → None.
def resolve_user(id_type: str, value: str) -> dict | None:
  """
  id_type: "customer_id" | "phone"
  {
      "user_id": "7f3a1e2c-...",
      "merged_ids": ["9b210f4d-...", "c4e0..."]   # may be empty
  }
  """

# ── analytics: profiles ───────────────────────────────────────
# A single user_id may yield several tracking_ids (created per device).
# Raises UpstreamError / RateLimitError.
def get_tracking_ids(user_id: str) -> dict:
  """
  {
      "profiles": [
          {"user_id": "7f3a1e2c-...", "tracking_id": 128934},
          {"user_id": "7f3a1e2c-...", "tracking_id": 559012}
      ]
  }
  """


# ── analytics: events ──  one tracking_id → its events
# Raises UpstreamError / RateLimitError.
def get_events(tracking_id: int) -> dict:
  """
  {
      "events": [
          {
              "event_type": "loan_application_submitted",
              "event_time": "2026-07-01 12:04:11.221000",
              "user_id": "7f3a1e2c-...",
              "session_id": 1719830400000,
              "properties": {"application_id": "APP-771", "screen": "loan_form"}
          },
          {"event_type": "session_start", "event_time": "...", ...}
      ]
  }
  """