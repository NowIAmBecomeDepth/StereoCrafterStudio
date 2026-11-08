# Vendored from AllTracker (https://github.com/aharley/alltracker, e755313), MIT, Copyright (c) 2025
# Adam W. Harley -- see LICENSE here and THIRD_PARTY_NOTICES.md. Edits for StereoCrafter Studio: only the helper the network uses; posenc (for the sin-motion options, off in the released model) raises.

import torch


def get_1d_sincos_pos_embed_from_grid(embed_dim, positions):
    assert embed_dim % 2 == 0
    omega = torch.arange(embed_dim // 2, dtype=torch.double)
    omega /= embed_dim / 2.0
    omega = 1.0 / 10000**omega  # (D/2,)

    positions = positions.reshape(-1)  # (M,)
    out = torch.einsum("m,d->md", positions, omega)  # (M, D/2), outer product

    emb_sin = torch.sin(out)  # (M, D/2)
    emb_cos = torch.cos(out)  # (M, D/2)

    emb = torch.cat([emb_sin, emb_cos], dim=1)  # (M, D)
    return emb[None].float()


def posenc(*args, **kwargs):
    raise NotImplementedError("AllTracker's sin-motion options aren't vendored")
