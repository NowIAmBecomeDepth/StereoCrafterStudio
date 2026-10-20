from typing import Union, Tuple

import torch
from diffusers import UNetSpatioTemporalConditionModel
from diffusers.models.unets.unet_spatio_temporal_condition import UNetSpatioTemporalConditionOutput


#: Input values per feed-forward slice (16M: a 32 MB fp16 slice, ~256 MB for
#: GEGLU's 8x-wide middle). See chunk_feed_forward.
FF_CHUNK_ELEMENTS = 16 * 2**20


def chunk_feed_forward(model: torch.nn.Module, budget: int = FF_CHUNK_ELEMENTS) -> int:
    """Run every diffusers FeedForward in ``model`` over slices of its first
    dimension. A feed-forward is per token, so the result is the same maths
    (to rounding: mean |diff| 0.0005 of the depth range over a 235-frame clip);
    only its peak memory changes. At window 120, 768x448, the temporal
    blocks' GEGLU was the whole run's peak: 13.5 GB live, so the card filled,
    the desktop and Studio's window were starved and DepthCrafter itself slowed
    (user, 2026-10-01). Sliced: 9.3 GB live, and 6.7 s/step against 7.3.
    Returns how many layers were wrapped."""
    from diffusers.models.attention import FeedForward

    def wrap(ff):
        plain = ff.forward

        def forward(hidden_states, *args, **kwargs):
            n0 = hidden_states.shape[0]
            step = max(1, budget // max(1, hidden_states[0].numel()))
            if step >= n0:
                return plain(hidden_states, *args, **kwargs)
            return torch.cat([plain(h, *args, **kwargs) for h in hidden_states.split(step, 0)], 0)

        ff.forward = forward

    wrapped = 0
    for module in model.modules():
        if isinstance(module, FeedForward):
            wrap(module)
            wrapped += 1
    return wrapped


class DiffusersUNetSpatioTemporalConditionModelDepthCrafter(
    UNetSpatioTemporalConditionModel
):

    def forward(
        self,
        sample: torch.Tensor,
        timestep: Union[torch.Tensor, float, int],
        encoder_hidden_states: torch.Tensor,
        added_time_ids: torch.Tensor,
        return_dict: bool = True,
    ) -> Union[UNetSpatioTemporalConditionOutput, Tuple]:

        # 1. time
        timesteps = timestep
        if not torch.is_tensor(timesteps):
            # TODO: this requires sync between CPU and GPU. So try to pass timesteps as tensors if you can
            # This would be a good case for the `match` statement (Python 3.10+)
            is_mps = sample.device.type == "mps"
            if isinstance(timestep, float):
                dtype = torch.float32 if is_mps else torch.float64
            else:
                dtype = torch.int32 if is_mps else torch.int64
            timesteps = torch.tensor([timesteps], dtype=dtype, device=sample.device)
        elif len(timesteps.shape) == 0:
            timesteps = timesteps[None].to(sample.device)

        # broadcast to batch dimension in a way that's compatible with ONNX/Core ML
        batch_size, num_frames = sample.shape[:2]
        timesteps = timesteps.expand(batch_size)

        t_emb = self.time_proj(timesteps)

        # `Timesteps` does not contain any weights and will always return f32 tensors
        # but time_embedding might actually be running in fp16. so we need to cast here.
        # there might be better ways to encapsulate this.
        t_emb = t_emb.to(dtype=self.conv_in.weight.dtype)

        emb = self.time_embedding(t_emb)  # [batch_size * num_frames, channels]

        time_embeds = self.add_time_proj(added_time_ids.flatten())
        time_embeds = time_embeds.reshape((batch_size, -1))
        time_embeds = time_embeds.to(emb.dtype)
        aug_emb = self.add_embedding(time_embeds)
        emb = emb + aug_emb

        # Flatten the batch and frames dimensions
        # sample: [batch, frames, channels, height, width] -> [batch * frames, channels, height, width]
        sample = sample.flatten(0, 1)
        # Repeat the embeddings num_video_frames times
        # emb: [batch, channels] -> [batch * frames, channels]
        emb = emb.repeat_interleave(num_frames, dim=0)
        # encoder_hidden_states: [batch, frames, channels] -> [batch * frames, 1, channels]
        encoder_hidden_states = encoder_hidden_states.flatten(0, 1).unsqueeze(1)

        # 2. pre-process
        sample = sample.to(dtype=self.conv_in.weight.dtype)
        assert sample.dtype == self.conv_in.weight.dtype, (
            f"sample.dtype: {sample.dtype}, "
            f"self.conv_in.weight.dtype: {self.conv_in.weight.dtype}"
        )
        sample = self.conv_in(sample)

        image_only_indicator = torch.zeros(
            batch_size, num_frames, dtype=sample.dtype, device=sample.device
        )

        down_block_res_samples = (sample,)
        for downsample_block in self.down_blocks:
            if (
                hasattr(downsample_block, "has_cross_attention")
                and downsample_block.has_cross_attention
            ):
                sample, res_samples = downsample_block(
                    hidden_states=sample,
                    temb=emb,
                    encoder_hidden_states=encoder_hidden_states,
                    image_only_indicator=image_only_indicator,
                )

            else:
                sample, res_samples = downsample_block(
                    hidden_states=sample,
                    temb=emb,
                    image_only_indicator=image_only_indicator,
                )

            down_block_res_samples += res_samples

        # 4. mid
        sample = self.mid_block(
            hidden_states=sample,
            temb=emb,
            encoder_hidden_states=encoder_hidden_states,
            image_only_indicator=image_only_indicator,
        )

        # 5. up
        for i, upsample_block in enumerate(self.up_blocks):
            res_samples = down_block_res_samples[-len(upsample_block.resnets) :]
            down_block_res_samples = down_block_res_samples[
                : -len(upsample_block.resnets)
            ]

            if (
                hasattr(upsample_block, "has_cross_attention")
                and upsample_block.has_cross_attention
            ):
                sample = upsample_block(
                    hidden_states=sample,
                    res_hidden_states_tuple=res_samples,
                    temb=emb,
                    encoder_hidden_states=encoder_hidden_states,
                    image_only_indicator=image_only_indicator,
                )
            else:
                sample = upsample_block(
                    hidden_states=sample,
                    res_hidden_states_tuple=res_samples,
                    temb=emb,
                    image_only_indicator=image_only_indicator,
                )

        # 6. post-process
        sample = self.conv_norm_out(sample)
        sample = self.conv_act(sample)
        sample = self.conv_out(sample)

        # 7. Reshape back to original shape
        sample = sample.reshape(batch_size, num_frames, *sample.shape[1:])

        if not return_dict:
            return (sample,)

        return UNetSpatioTemporalConditionOutput(sample=sample)
