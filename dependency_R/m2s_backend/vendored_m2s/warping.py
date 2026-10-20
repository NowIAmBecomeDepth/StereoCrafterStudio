"""
Copyright 2026 Google LLC

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    https://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
"""

import numpy as np


def scatter_image(
    input_frame: np.ndarray,
    inverse_depth: np.ndarray,
    direction: int,
    scale_factor: float,
    inverse_ordering: bool = False,
    reproject_depth: bool = False,
):
  h, w = input_frame.shape[:2]
  disparity_map = (inverse_depth.astype(np.float32) * scale_factor).astype(
      np.float32
  )
  disparity_map_int = disparity_map.astype(np.int32)
  weight_for_plus1 = disparity_map - disparity_map_int.astype(np.float32)
  disparity_map_int_plus1 = (disparity_map + 1.0).astype(np.int32)

  x_coords, _ = np.meshgrid(np.arange(w), np.arange(h))
  reproj_x_coords = x_coords + (disparity_map_int * direction)
  reproj_x_coords_plus1 = x_coords + (disparity_map_int_plus1 * direction)

  reproj_img = np.zeros_like(input_frame).astype(np.float32)
  reproj_img_weight = np.zeros_like(input_frame).astype(np.float32)
  filled_pixel_mask = np.zeros((h, w)).astype(bool)

  valid_mask = (reproj_x_coords >= 0) & (reproj_x_coords < w)
  valid_y, valid_x = np.where(valid_mask)
  reproj_valid_x_coords = reproj_x_coords[valid_y, valid_x]
  valid_mask1 = (reproj_x_coords_plus1 >= 0) & (reproj_x_coords_plus1 < w)
  valid_y1, valid_x1 = np.where(valid_mask1)
  reproj_valid_x_coords_plus1 = reproj_x_coords_plus1[valid_y1, valid_x1]

  if inverse_ordering:
    valid_y = valid_y[::-1]
    valid_x = valid_x[::-1]
    reproj_valid_x_coords = reproj_valid_x_coords[::-1]
    valid_y1 = valid_y1[::-1]
    valid_x1 = valid_x1[::-1]
    reproj_valid_x_coords_plus1 = reproj_valid_x_coords_plus1[::-1]

  reproj_img[valid_y, reproj_valid_x_coords] += (
      input_frame[valid_y, valid_x]
      * (1.0 - weight_for_plus1[valid_y, valid_x])[:, None]
  )
  reproj_img_weight[valid_y, reproj_valid_x_coords] += (
      1.0 - weight_for_plus1[valid_y, valid_x]
  )[:, None]
  reproj_img[valid_y1, reproj_valid_x_coords_plus1] += (
      input_frame[valid_y1, valid_x1]
      * weight_for_plus1[valid_y1, valid_x1][:, None]
  )
  reproj_img_weight[valid_y1, reproj_valid_x_coords_plus1] += weight_for_plus1[
      valid_y1, valid_x1
  ][:, None]

  filled_pixel_mask[(reproj_img_weight != 0)[:, :, 0]] = 1
  reproj_img[reproj_img_weight != 0] /= reproj_img_weight[
      reproj_img_weight != 0
  ]

  if reproject_depth:
    depth = 1 / (inverse_depth + 1e-6)
    reprojected_depth = np.zeros_like(depth, dtype=np.float32)
    reprojected_depth_weight = np.zeros_like(depth, dtype=np.float32)

    reprojected_depth[valid_y, reproj_valid_x_coords] += depth[
        valid_y, valid_x
    ] * (1.0 - weight_for_plus1[valid_y, valid_x])
    reprojected_depth_weight[valid_y, reproj_valid_x_coords] += (
        1.0 - weight_for_plus1[valid_y, valid_x]
    )
    reprojected_depth[valid_y1, reproj_valid_x_coords_plus1] += (
        depth[valid_y1, valid_x1] * weight_for_plus1[valid_y1, valid_x1]
    )
    reprojected_depth_weight[
        valid_y1, reproj_valid_x_coords_plus1
    ] += weight_for_plus1[valid_y1, valid_x1]

    reprojected_depth[
        reprojected_depth_weight != 0
    ] /= reprojected_depth_weight[reprojected_depth_weight != 0]
  else:
    reprojected_depth = None

  black_y, black_new_x = np.where(filled_pixel_mask == 0)
  black_pixel_indexes = np.ravel_multi_index(
      (black_y, black_new_x), dims=(h, w)
  )
  mask = np.zeros(input_frame.shape[:2], dtype=np.uint8)
  if black_pixel_indexes.ndim == 1:
    y_coords, x_coords = np.divmod(black_pixel_indexes, w)
  else:
    y_coords, x_coords = black_pixel_indexes[:, 0], black_pixel_indexes[:, 1]
  mask[y_coords, x_coords] = 255

  return reproj_img.astype(input_frame.dtype), mask, reprojected_depth

