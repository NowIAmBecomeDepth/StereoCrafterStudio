"""DVD (Deterministic Video Depth Estimation with Generative Priors) -- the model code Studio runs.

Vendored from https://github.com/Billynom8/DVD (a fork of EnVision-Research/DVD; Apache-2.0, LICENSE here),
which builds on DiffSynth-Studio's Wan2.1 code. Only the four files inference needs are kept, untouched:
the Wan2.1 DiT and video VAE and the two small modules they import. The pipeline, the text encoder and the
training code are not: core/dvd/model.py runs the one forward pass DVD makes (no prompt, so no T5).

The weights (FayeHongfeiZhang/DVD, CC-BY-NC-4.0) are a download into the models folder, never shipped.
"""
