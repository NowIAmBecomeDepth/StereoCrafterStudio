'''
    This file is to inference arbitrary video files for Shot Cut
'''
import os, sys, shutil
import argparse
import numpy as np
import copy
import json
import torch
import warnings
warnings.filterwarnings("ignore", category=UserWarning)


# Import files from the local folder
root_path = os.path.abspath('.')
sys.path.append(root_path)
from omnishotcut.architecture.backbone import build_backbone
from omnishotcut.architecture.transformer import build_transformer
from omnishotcut.architecture.model import OmniShotCut
from omnishotcut.datasets.transforms import Video_Augmentation_Transform
from omnishotcut.datasets.utils import _decode_video, _video_fps
from omnishotcut.util.visualization import visualize_concated_frames
from omnishotcut.label_correspondence import unique_intra_label_mapping, unique_inter_label_mapping, intra_int2string, inter_int2string


# Video Transform
video_transform = Video_Augmentation_Transform(set_type = "val")




def load_model(checkpoint_path: str, device: str = "cuda"):
    # SceneCutGUI: added the `device` argument (upstream hardcodes "cuda") so the
    # GUI's device selector can fall back to cpu/mps.


    # Check the checkpoint
    checkpoint_path = os.path.abspath(checkpoint_path)
    if not os.path.exists(checkpoint_path):
        raise FileNotFoundError(f"Checkpoint not found: {checkpoint_path}")


    # Load state dict
    state_dict = torch.load(checkpoint_path, map_location="cpu", weights_only=False)
    if "args" not in state_dict or "model" not in state_dict:
        raise ValueError("Checkpoint must contain keys: 'args' and 'model'.")


    # Load the model
    model_args = state_dict["args"]
    backbone = build_backbone(model_args)
    transformer = build_transformer(model_args)
    model = OmniShotCut(
                            backbone,
                            transformer,
                            num_intra_relation_classes = model_args.num_intra_relation_classes,
                            num_inter_relation_classes = model_args.num_inter_relation_classes,
                            num_frames = model_args.max_process_window_length,
                            num_queries = model_args.num_queries,
                            aux_loss = model_args.aux_loss,
                        )
    model.load_state_dict(state_dict["model"], strict=True)
    model.to(device)
    model.eval()


    return model, model_args



def split_videos(video, chunk_size, overlap_size):

    assert video.ndim == 4, "video must be (T, H, W, C)"
    assert overlap_size >= 0 and overlap_size < chunk_size

    T, H, W, C = video.shape
    stride = chunk_size - overlap_size

    # Form the return list
    return_list = []
    window_start_idx = 0

    while window_start_idx < T:

        window_end_idx = window_start_idx + chunk_size
        valid_len = min(chunk_size, T - window_start_idx)

        # Fetch current window
        chunk = video[window_start_idx:min(window_end_idx, T)]

        # Padding
        num_pad_frames = chunk_size - valid_len
        if num_pad_frames > 0:
            black = np.zeros((num_pad_frames, H, W, C), dtype=video.dtype)
            chunk = np.concatenate([chunk, black], axis=0)

        # Valid region for this window. We split the overlap region by half.
        left_overlap = overlap_size // 2
        right_overlap = overlap_size - left_overlap

        if window_start_idx == 0:
            valid_start_idx = 0
        else:
            valid_start_idx = window_start_idx + left_overlap

        if window_end_idx >= T:
            valid_end_idx = T
        else:
            valid_end_idx = window_end_idx - right_overlap

        return_list.append(
            [
                chunk,
                num_pad_frames,
                window_start_idx,
                valid_start_idx,
                valid_end_idx,
                valid_len,
            ]
        )

        # End
        if window_end_idx >= T:
            break

        window_start_idx += stride

    return return_list



def merge_predictions(pred_boundary_full, pred_boundary, duplicate_tolerance=2):

    # Sort
    pred_boundary = sorted(pred_boundary, key=lambda x: x["end_frame_idx"])

    # Merge
    for item in pred_boundary:

        # Check duplicate
        if len(pred_boundary_full) != 0:
            last_end_frame_idx = pred_boundary_full[-1]["end_frame_idx"]
            if abs(item["end_frame_idx"] - last_end_frame_idx) <= duplicate_tolerance:
                continue

        pred_boundary_full.append(item)

    return pred_boundary_full



def single_video_inference(video_path, model, model_args, overlap_window_length):


    # Init the parameter
    max_process_window_length = model_args.max_process_window_length
    process_height, process_width = model_args.process_height, model_args.process_width


    # Read the Video
    video_np_full = _decode_video(video_path, process_width, process_height)
    fps = _video_fps(video_path)
    

    # Iterate all the clips
    pred_boundary_full = []

    for clip_idx, (video_np, num_pad_frames, window_start_idx, valid_start_idx, valid_end_idx, valid_len) in enumerate(split_videos(video_np_full, max_process_window_length, overlap_window_length)):

        # Transform
        video_tensor = video_transform(video_np).unsqueeze(0).to("cuda")


        # Inference
        with torch.inference_mode():
            outputs = model(video_tensor)
        

        # Choose the label with max value
        probas_intra = outputs['intra_clip_logits'].softmax(-1)[0, :, :-1] 
        probas_inter = outputs['inter_clip_logits'].softmax(-1)[0, :, :-1]  
        range_probas = outputs['pred_shot_logits'].softmax(-1)[0, :, :-1]  
        query_intra_idx = probas_intra.argmax(dim=-1)
        query_inter_idx = probas_inter.argmax(dim=-1)
        query_range_idx = range_probas.argmax(dim=-1)


        pred_boundary = []
        start_frame_idx_local = 0

        for keep_idx in range(len(query_intra_idx)):

            # Fetch Label
            pred_intra_label = int(query_intra_idx[keep_idx].detach().cpu())
            pred_inter_label = int(query_inter_idx[keep_idx].detach().cpu())

            # Convert ranges from local window scale to video duration scale
            end_frame_idx_local = int(query_range_idx[keep_idx].detach().cpu())
            end_frame_idx_local = min(end_frame_idx_local, valid_len)

            pred_range = [start_frame_idx_local, end_frame_idx_local]
            pred_range_global = [
                window_start_idx + start_frame_idx_local,
                window_start_idx + end_frame_idx_local,
            ]

            # Sometimes model outputs the same start/end. Skip to avoid invalid range.
            if start_frame_idx_local >= end_frame_idx_local:
                continue

            # Append only the boundary inside the valid region
            end_frame_idx_global = window_start_idx + end_frame_idx_local

            if valid_start_idx < end_frame_idx_global <= valid_end_idx:
                pred_boundary.append(
                    {
                        "end_frame_idx": int(end_frame_idx_global),
                        "intra_label": int(pred_intra_label),
                        "inter_label": int(pred_inter_label),
                    }
                )

            start_frame_idx_local = end_frame_idx_local

            # End
            if end_frame_idx_local >= valid_len:
                break

        # Merge predicted results; here pred_boundary are already valid
        pred_boundary_full = merge_predictions(
            pred_boundary_full,
            pred_boundary,
        )


    # Convert boundary to range
    pred_ranges_full = []
    pred_intra_labels_full = []
    pred_inter_labels_full = []

    start_frame_idx_local = 0

    for item in pred_boundary_full:

        end_frame_idx = min(int(item["end_frame_idx"]), len(video_np_full))

        if end_frame_idx <= start_frame_idx_local:
            continue

        pred_ranges_full.append(
            [
                int(start_frame_idx_local),
                int(end_frame_idx),
            ]
        )
        pred_intra_labels_full.append(int(item["intra_label"]))
        pred_inter_labels_full.append(int(item["inter_label"]))

        start_frame_idx_local = end_frame_idx


    return pred_ranges_full, pred_intra_labels_full, pred_inter_labels_full, video_np_full, fps



def _run_on_numpy(video_np, model, model_args, overlap_window_length):
    """Run inference on a pre-loaded numpy array (T, H, W, 3).
    Returns (ranges, intra_labels, inter_labels).
    """
    max_process_window_length = model_args.max_process_window_length

    # SceneCutGUI: follow the model's own device instead of hardcoding "cuda", so this
    # cannot drift out of sync with the device chosen in load_model().
    device = next(model.parameters()).device

    pred_boundary_full = []

    for clip_idx, (video_chunk, num_pad_frames, window_start_idx, valid_start_idx, valid_end_idx, valid_len) in enumerate(split_videos(video_np, max_process_window_length, overlap_window_length)):

        video_tensor = video_transform(video_chunk).unsqueeze(0).to(device)

        with torch.inference_mode():
            outputs = model(video_tensor)

        probas_intra = outputs['intra_clip_logits'].softmax(-1)[0, :, :-1]
        probas_inter = outputs['inter_clip_logits'].softmax(-1)[0, :, :-1]
        range_probas = outputs['pred_shot_logits'].softmax(-1)[0, :, :-1]
        query_intra_idx = probas_intra.argmax(dim=-1)
        query_inter_idx = probas_inter.argmax(dim=-1)
        query_range_idx = range_probas.argmax(dim=-1)

        pred_boundary = []
        start_frame_idx_local = 0

        for keep_idx in range(len(query_intra_idx)):

            pred_intra_label = int(query_intra_idx[keep_idx].detach().cpu())
            pred_inter_label = int(query_inter_idx[keep_idx].detach().cpu())

            end_frame_idx_local = int(query_range_idx[keep_idx].detach().cpu())
            end_frame_idx_local = min(end_frame_idx_local, valid_len)

            if start_frame_idx_local >= end_frame_idx_local:
                continue

            end_frame_idx_global = window_start_idx + end_frame_idx_local

            if valid_start_idx < end_frame_idx_global <= valid_end_idx:
                pred_boundary.append(
                    {
                        "end_frame_idx": int(end_frame_idx_global),
                        "intra_label": int(pred_intra_label),
                        "inter_label": int(pred_inter_label),
                    }
                )

            start_frame_idx_local = end_frame_idx_local

            if end_frame_idx_local >= valid_len:
                break

        pred_boundary_full = merge_predictions(pred_boundary_full, pred_boundary)

    pred_ranges = []
    pred_intra_labels = []
    pred_inter_labels = []
    start_frame_idx = 0

    for item in pred_boundary_full:
        end_frame_idx = min(int(item["end_frame_idx"]), len(video_np))
        if end_frame_idx <= start_frame_idx:
            continue
        pred_ranges.append([int(start_frame_idx), int(end_frame_idx)])
        pred_intra_labels.append(int(item["intra_label"]))
        pred_inter_labels.append(int(item["inter_label"]))
        start_frame_idx = end_frame_idx

    return pred_ranges, pred_intra_labels, pred_inter_labels


