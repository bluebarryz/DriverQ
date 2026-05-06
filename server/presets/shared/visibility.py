"""Camera-visibility filters shared by presets.

Both filters use OR semantics across the camera list and apply per-instance:
- `appears_in_visible_cameras`: each instance must appear in at least one frame
  of any of the listed cameras within [f0, f1].
- `any_visibility_in_hidden_cameras`: any instance appearing in any frame of
  any of the listed cameras within [f0, f1] disqualifies the match.
"""


def appears_in_visible_cameras(conn, scene_token, instance_tokens, cameras, f0, f1):
    if not cameras:
        return True
    cam_ph = ",".join(["?"] * len(cameras))
    for tok in instance_tokens:
        row = conn.execute(
            f"""SELECT 1 FROM visibility
                WHERE scene_token = ? AND instance_token = ?
                  AND camera IN ({cam_ph})
                  AND frame_idx >= ? AND frame_idx <= ?
                LIMIT 1""",
            (scene_token, tok, *cameras, f0, f1),
        ).fetchone()
        if row is None:
            return False
    return True


def any_visibility_in_hidden_cameras(conn, scene_token, instance_tokens, cameras, f0, f1):
    if not cameras or not instance_tokens:
        return False
    cam_ph = ",".join(["?"] * len(cameras))
    inst_ph = ",".join(["?"] * len(instance_tokens))
    row = conn.execute(
        f"""SELECT 1 FROM visibility
            WHERE scene_token = ?
              AND instance_token IN ({inst_ph})
              AND camera IN ({cam_ph})
              AND frame_idx >= ? AND frame_idx <= ?
            LIMIT 1""",
        [scene_token, *instance_tokens, *cameras, f0, f1],
    ).fetchone()
    return row is not None
