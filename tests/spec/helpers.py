from datetime import datetime
from pathlib import Path

NOW = datetime(2026, 11, 10, 12, 0, 0)
PINNED_SPEC = Path(__file__).parents[2] / "fixtures" / "batches" / "spec.yaml"


def make_row(i: int, **over: str) -> dict[str, str]:
    row = {
        "unique_key": str(1000 + i),
        "created_date": "2026-11-09T10:00:00.000",
        "closed_date": "2026-11-09T11:00:00.000",
        "agency": "DOT",
        "complaint_type": "Street Condition",
        "descriptor": "Pothole",
        "location_type": "Street",
        "incident_zip": "11201",
        "status": "Closed",
        "community_board": "02 BROOKLYN",
        "borough": "BROOKLYN",
        "latitude": "40.6",
        "longitude": "-73.9",
        "x_coordinate_state_plane": "1001234",
        "y_coordinate_state_plane": "190000",
        "open_data_channel_type": "PHONE",
    }
    row.update(over)
    return {k: v for k, v in row.items() if v != "<omit>"}
