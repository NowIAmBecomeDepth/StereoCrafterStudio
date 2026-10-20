# Third-party notices

Code from other projects that is vendored into this repository rather than
installed from a package index, with the licence each one carries. Everything
else this project depends on is installed by `uv` and carries its own licence in
site-packages.

## AutoShot — `third_party/AutoShot/`

Shot-boundary detection network, used by Scene Cut's AutoShot detector.

- Upstream: https://github.com/wentaozhu/AutoShot
- Licence: MIT — `third_party/AutoShot/LICENSE`
- Vendored because it is not published to PyPI and its model file imports its
  own modules by bare name.
- Only the two files inference needs are kept (`linear.py` and the supernet
  definition); the training, evaluation and benchmark data are not.

## OmniShotCut — `third_party/OmniShotCut/`

Shot-Query Transformer for shot boundary and transition detection, used by Scene
Cut's OmniShotCut detector.

- Upstream: https://github.com/UVA-Computer-Vision-Lab/OmniShotCut
- Licence: MIT — `third_party/OmniShotCut/LICENSE`
- Vendored rather than installed because upstream's `requirements.txt` pins
  `transformers==4.57.3`, which would downgrade the version the DINOv3
  validation and the rest of this suite depend on.
- Carries the local edits SceneCutGUI made, marked in the source: the ResNet
  backbone is built with `pretrained=False` (the checkpoint overwrites every
  backbone weight anyway, so the download is wasted and breaks offline loading),
  and the two hardcoded `.to("cuda")` calls are replaced so the device selector
  works.

## SAM 3 — `third_party/sam3/`

Meta's Segment Anything Model 3, used by the Depth Map Editor to select and track
objects.

- Upstream: https://github.com/facebookresearch/sam3 (commit `2345a4a`,
  2026-09-18)
- Licence: SAM License — `third_party/sam3/LICENSE`. Redistribution must carry
  that agreement, which it does.
- Only the inference half is kept: the 66 modules `Sam3VideoPredictor` imports
  plus the BPE vocabulary. Training, evaluation and agent code are left out.
  Files are byte-identical to upstream so a later release can be re-synced by
  diffing; everything Studio-side lives in `core/depthedit/sam3_backend.py`.
- Vendored rather than installed because upstream's package pins `numpy<2`,
  while this suite runs numpy 2.x; the code itself runs on numpy 2.
- The model weights are not included. They are downloaded separately from
  https://huggingface.co/facebook/sam3 under the same licence.

## SceneCutGUI

The Scene Cut tool itself (`studio/stages/scenecut/`, `core/scenecut/`) is ported
from enoky's SceneCutGUI, MIT licensed.

- Upstream: https://github.com/enoky/SceneCutGUI

## M2S warp and mask -- `dependency_R/m2s_backend/`

Splatting's M2S warp and mask options (`core/splatting/m2s_backends.py`).

- Adapted from M2SVID (google-research/m2svid; research by Nina Shvetsova et al., 3DV 2026), by way of
  Archit's M2S GUI.
- Licence: Apache License 2.0 -- the source headers name Google LLC; the licence text is in
  `dependency_R/notices/LICENSE`, with the bundle's notes in `dependency_R/notices/`.
- Studio falls back to its standard warp if this folder is missing.

## Auto Convergence -- `dependency/convergence_estimator.py`, `dependency/u2net.py`, `dependency/iw3_sod_v1_20260122.pth`

Splatting's Auto Convergence (the salient-object estimate), added by enoky.

- The estimator and its model, `iw3_sod_v1_20260122.pth`, are from nagadomi's nunif / iw3
  (https://github.com/nagadomi/nunif; the model is its release asset of 2026-01-22). MIT licence,
  Copyright (C) 2019-2023 nagadomi.
- `u2net.py` is U²-Net's model code (https://github.com/xuebinqin/U-2-Net; Xuebin Qin et al., Pattern
  Recognition 2020). Apache License 2.0 -- the licence text is in `dependency_R/notices/LICENSE`.

## Forward warp -- `dependency/forward_warp_pytorch/`

Splatting's standard warp.

- From Forward-Warp (https://github.com/lizhihao6/Forward-Warp), as Tencent's StereoCrafter uses it; ported to
  plain PyTorch by irgadget (2025). MIT licence, Copyright (c) 2019 lizhihao6.

## Models

None of these model weights are in this repository (the only one that is: Auto Convergence's, above): the installer or Studio downloads them, each under its own
licence. **The StereoCrafter, StereoCrafter2 and DepthCrafter models -- and the StereoCrafter inference code -- are
for academic, research and education purposes only, with no commercial or production use.**

| Model | Source | Licence |
| ----- | ------ | ------- |
| DepthCrafter | https://huggingface.co/tencent/DepthCrafter (https://github.com/Tencent/DepthCrafter) | Tencent's terms, academic / research / education only -- `LICENSE` in `weights/DepthCrafter`. Fine-tuned with Stable Video Diffusion. Studio's `depthcrafter/` inference code comes from it by way of Billynom8's DepthCrafter GUI Seg. |
| StereoCrafter | https://huggingface.co/TencentARC/StereoCrafter | `License-Code.txt` (inference code and weights): academic / research / education only. |
| StereoCrafter2 | https://huggingface.co/TencentARC/StereoCrafter2 | Tencent's terms, academic / research / education only -- `LICENSE` in `weights/StereoCrafter2`. Built on Wan 2.1 VACE (Apache 2.0). |
| Stable Video Diffusion img2vid-xt 1.1 | https://huggingface.co/stabilityai/stable-video-diffusion-img2vid-xt-1-1 (gated) | Stability AI Community License -- `LICENSE.md` in its folder. |
| Wan 2.1 VACE 14B (diffusers) | https://huggingface.co/Wan-AI | Apache 2.0. |
| SAM 3 | https://huggingface.co/facebook/sam3 (gated) | SAM License (see SAM 3 above). |
| ROSE | https://github.com/Kunbyte-AI/ROSE, fetched by *Set Up ROSE* into its own environment | Apache 2.0 -- `LICENSE` in `weights/ROSE`. |
| AutoShot, TransNetV2, OmniShotCut, DINOv3, SSCD checkpoints | the SceneCutGUI checkpoint bundle (see the README) | each under its own project's terms. |

## Installed, not included

Installed by `uv` (licences in site-packages) or by the user, named here because their terms differ from the rest:
PySide6 / Qt (LGPL v3), mmgp (GPL v3; only Inpainting V2's optional MMGP streaming mode uses it), SageAttention
(installed from its GitHub release), and FFmpeg (installed by the user; not distributed with Studio).
