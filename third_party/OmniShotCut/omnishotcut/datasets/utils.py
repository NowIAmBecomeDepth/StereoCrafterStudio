'''
    Util for collate function and related needs
'''
from typing import Optional, List
from fractions import Fraction
import numpy as np
import cv2
import ffmpeg
import torch
from torch import Tensor

from ..util.misc import NestedTensor


def _decode_video(path, width, height):
    try:
        stream, _ = (
            ffmpeg.input(path)
            .output("pipe:", format="rawvideo", pix_fmt="rgb24", s=f"{width}x{height}", vsync="passthrough")
            .run(capture_stdout=True, capture_stderr=True)
        )
    except ffmpeg.Error as e:
        raise RuntimeError(f"ffmpeg failed to decode: {path}\n{e.stderr.decode('utf-8', 'ignore') if e.stderr else ''}") from e
    video_np = np.frombuffer(stream, np.uint8).reshape(-1, height, width, 3)
    if len(video_np) == 0:
        raise ValueError(f"decoded 0 frames: {path}")
    return video_np


def _video_fps(path):
    s = next(x for x in ffmpeg.probe(path)["streams"] if x["codec_type"] == "video")
    r = s.get("avg_frame_rate", "0/0")
    if r in ("0/0", "0"):
        r = s.get("r_frame_rate", "0/1")
    return float(Fraction(r))


def _resize_video(video_np, width, height):
    """Resize (T, H, W, 3) uint8 to (T, height, width, 3). No-op if already that size."""
    if video_np.shape[1] == height and video_np.shape[2] == width:
        return video_np
    out = np.empty((video_np.shape[0], height, width, 3), dtype=np.uint8)
    for i in range(video_np.shape[0]):
        out[i] = cv2.resize(video_np[i], (width, height), interpolation=cv2.INTER_AREA)
    return out





def _max_by_axis(the_list):
    # type: (List[List[int]]) -> List[int]
    maxes = the_list[0]
    for sublist in the_list[1:]:
        for index, item in enumerate(sublist):
            maxes[index] = max(maxes[index], item)
    return maxes



def nested_tensor_from_tensor_list(tensor_list: List[Tensor], split=True):
    # Modified from VisTR, which shows a possible solution to handle video inputs

    # Split all video frames to one list, like an image form
    if split:
        # tensor_list = [tensor.split(3, dim=0) for tensor in tensor_list]            
        tensor_list = [item for sublist in tensor_list for item in sublist]           # The length of tensor_list equals to Batch Size * #Frames


    # Process each single one
    if tensor_list[0].ndim == 3:                # Expected (C, H, W) dimension

        # Same as DETR
        max_size = _max_by_axis([list(img.shape) for img in tensor_list])
        # min_size = tuple(min(s) for s in zip(*[img.shape for img in tensor_list]))
        batch_shape = [len(tensor_list)] + max_size
        b, c, h, w = batch_shape
        dtype = tensor_list[0].dtype
        device = tensor_list[0].device
        tensor = torch.zeros(batch_shape, dtype=dtype, device=device)
        mask = torch.ones((b, h, w), dtype=torch.bool, device=device)

        # Add Padding
        for img, pad_img, m in zip(tensor_list, tensor, mask):
            pad_img[: img.shape[0], : img.shape[1], : img.shape[2]].copy_(img)
            m[: img.shape[1], :img.shape[2]] = False

    else:

        raise ValueError('not supported')


    # Return Nested Tensor Form
    return NestedTensor(tensor, mask)           # tensor shape is (B*F, C, H, W) and mask shape is (B*F, H, W)



def collate_fn(batch):
    
    batch = list(zip(*batch))
    batch[0] = nested_tensor_from_tensor_list(batch[0])     # 0: Video Inputs;  1: GT Labels

    return tuple(batch)





