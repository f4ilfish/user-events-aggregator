"""Fresh fixture graph for every mock bundle; timestamps are UTC."""

def create_fixtures():
    users = {
        ("customer_id", "C-100"): {"user_id": "u-1", "merged_ids": ["u-2"]},
        ("phone", "+79990000001"): {"user_id": "u-1", "merged_ids": ["u-2"]},
        ("customer_id", "C-200"): {"user_id": "u-3", "merged_ids": []},
        ("phone", "+79990000002"): {"user_id": "u-3", "merged_ids": []},
        ("customer_id", "C-300"): {"user_id": "u-4", "merged_ids": []},
    }
    profiles = {
        "u-1": [
            {"user_id": "u-1", "tracking_id": 128934},
            {"user_id": "u-1", "tracking_id": 559012},
        ],
        "u-2": [{"user_id": "u-2", "tracking_id": 771003}],
        "u-3": [{"user_id": "u-3", "tracking_id": 880004}],
        "u-4": [],
    }

    def event(kind, time, user_id, session_id, properties):
        return dict(event_type=kind, event_time=time, user_id=user_id,
                    session_id=session_id, properties=properties)

    # Fixture event_time values are UTC; preserve the format from note.md.
    events = {
        128934: [
            event("session_start", "2026-07-01 12:00:00.000000", "u-1", 1782907200000, {}),
            event("loan_application_submitted", "2026-07-01 12:04:11.221000", "u-1",
                  1782907200000, {"application_id": "APP-771", "screen": "loan_form"}),
        ],
        559012: [event("session_start", "2026-07-02 09:00:00.000000", "u-1",
                       1782982800000, {"device": "mobile"})],
        771003: [event("session_start", "2026-06-30 08:00:00.000000", "u-2",
                       1782806400000, {"device": "desktop"})],
        880004: [],
    }
    return users, profiles, events
