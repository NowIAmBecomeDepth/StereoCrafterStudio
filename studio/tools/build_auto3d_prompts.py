"""build_auto3d_prompts.py -- core/splatting/auto3d_prompts.json, Auto 3D's six shot-scale classes.

Each class's prompts (Thomas's Auto 3D patch, 2026-10-31, after Rowan3D's nt_autostrength) are encoded by
CLIP ViT-B/32 laion400m_e32's text encoder, each L2-normalised, averaged per class and normalised again.
Run once; Studio itself only reads the JSON.

    python studio/tools/build_auto3d_prompts.py <open_clip checkpoint .pt> <folder with vocab.json + merges.txt>

The checkpoint is open_clip's vit_b_32-quickgelu-laion400m_e32-46683a32.pt; the tokenizer files are CLIP's
(huggingface.co/openai/clip-vit-base-patch32), the same BPE as open_clip's.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import torch
import torch.nn as nn
import torch.nn.functional as F

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from core.splatting.auto_estimate import PROMPTS, _state_dict, _Transformer  # noqa: E402

CLASSES = [
    (0.0, ["an extreme wide shot of a vast landscape", "an aerial view of a city skyline", "a distant panoramic view",
           "an establishing shot from far away", "a landscape seen from a great distance"]),
    (0.2, ["a wide shot", "a long shot of a scene", "a wide angle view of a place", "a street scene in a wide shot"]),
    (0.4, ["a full shot", "a full body shot of a person", "a view of a room",
           "a shot of a whole object from a few meters away"]),
    (0.55, ["a medium shot", "a medium shot of a person from the waist up", "a mid shot of people talking"]),
    (0.8, ["a close-up shot", "a close-up of a face", "a close-up of an object", "a close-up photo"]),
    (1.0, ["an extreme close-up macro shot", "a macro photo", "an extreme close-up of an eye",
           "a very close macro detail"]),
]
TEMPERATURE = 50.0
CALIB = [1.112057410582652, -0.044505877791842084]
CONTEXT = 77


class ClipText(nn.Module):
    """CLIP ViT-B/32's text encoder, open_clip's names."""

    def __init__(self, vocab=49408, width=512, layers=12, heads=8, out=512):
        super().__init__()
        self.token_embedding = nn.Embedding(vocab, width)
        self.positional_embedding = nn.Parameter(torch.empty(CONTEXT, width))
        self.transformer = _Transformer(width, layers, heads)
        self.ln_final = nn.LayerNorm(width)
        self.text_projection = nn.Parameter(torch.empty(width, out))

    def forward(self, tokens):
        x = self.token_embedding(tokens) + self.positional_embedding
        mask = torch.full((CONTEXT, CONTEXT), float("-inf")).triu(1)
        x = self.ln_final(self.transformer(x, mask))
        return x[torch.arange(x.shape[0]), tokens.argmax(dim=-1)] @ self.text_projection


def main(ckpt: str, tok_dir: str) -> None:
    from transformers import CLIPTokenizer
    tok = CLIPTokenizer(str(Path(tok_dir) / "vocab.json"), str(Path(tok_dir) / "merges.txt"))
    sd = _state_dict(torch.load(ckpt, map_location="cpu", weights_only=True))
    text_sd = {k: v.float() for k, v in sd.items() if not k.startswith("visual.") and k not in ("logit_scale",)
               and not k.startswith("attn_mask")}
    model = ClipText()
    model.load_state_dict(text_sd)
    model.eval()
    rows = []
    with torch.no_grad():
        for _v, prompts in CLASSES:
            # start, the prompt, end -- then 0s, as open_clip pads (its features are read at the end token,
            # the highest id, so the padding never counts)
            ids = [tok(p)["input_ids"][:CONTEXT] for p in prompts]
            t = torch.tensor([row + [0] * (CONTEXT - len(row)) for row in ids])
            e = F.normalize(model(t), dim=-1).mean(0)
            rows.append(F.normalize(e, dim=-1).tolist())
    out = {"model": "open_clip ViT-B-32-quickgelu laion400m_e32", "text": rows,
           "values": [v for v, _p in CLASSES], "temperature": TEMPERATURE, "calib": CALIB, "views": "3crop",
           "prompts": [p for _v, p in CLASSES]}
    PROMPTS.write_text(json.dumps(out, indent=1), encoding="utf-8")
    print(f"wrote {PROMPTS} ({len(rows)} classes)")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
