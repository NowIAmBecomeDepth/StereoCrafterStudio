"""SEA-RAFT optical flow (Princeton VL, BSD 3-Clause -- LICENSE here), vendored for Edge Fill's
Copy From Other Frames (core/removal/flow_fill.py). Only the model: training, datasets and the
Hugging Face loader are left out; the weights come from Settings > Studio > Models."""
from .raft import RAFT  # noqa: F401
