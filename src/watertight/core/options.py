from __future__ import annotations

from dataclasses import dataclass

from .io import MAX_FACES, MAX_FILE_BYTES


@dataclass
class RepairOptions:
    overwrite: bool = False
    merge_tol_rel: float = 1e-6  # vertex merge tolerance, times the bbox diagonal
    max_volume_change: float = 0.02  # a result outside this limit is not saved
    max_bbox_change: float = 0.001  # a result outside this limit is not saved
    max_far_share: float = 0.01  # not saved if more than 1 % of the surface moved too far
    far_tol_rel: float = 0.005  # "too far" = more than 0.5 % of the model diagonal
    refine: bool = False  # keep trying other settings until no better result exists
    refine_max_tries: int = 8
    pinch_cut_rings: int = 2  # faces around a pinch point that are cut out and refilled
    pinch_max_share: float = 0.3  # give up cutting if it would touch more than 30 % of the faces
    dust_face_count: int = 4  # shells with fewer faces are "dust"
    dust_diag_rel: float = 0.001  # shells smaller than this x model diagonal are "dust"
    max_file_bytes: int = MAX_FILE_BYTES
    max_faces: int = MAX_FACES
