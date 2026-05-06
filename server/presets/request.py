from pydantic import BaseModel


class QueryRequest(BaseModel):
    maneuver: str | None = None
    location: str | None = None
    ego_only: bool = False
    non_ego_only: bool = False
    preset: str | None = None
    ped_cross_frame_gap_max: int | None = None
    visible_cameras: list[str] | None = None
    hidden_cameras: list[str] | None = None
    occluded_frames_min: int | None = None
