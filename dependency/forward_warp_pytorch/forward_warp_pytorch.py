import torch
from torch.nn import Module
from torch.autograd import Function
import math

#: Pixels (frames x H x W) the forward pass warps at once: two 4K frames.
_CHUNK_PIXELS = 2 * 3840 * 2160


def _warp_chunk(im0, flow, im1, interpolation_mode_int):
    """Warp im0 [b, C, H, W] by flow [b, H, W, 2] into im1 (a view, zeroed)."""
    b, C, H, W = im0.shape
    HW = H * W
    xs = torch.arange(W, device=im0.device, dtype=im0.dtype).view(1, 1, W)
    ys = torch.arange(H, device=im0.device, dtype=im0.dtype).view(1, H, 1)
    x_dest = xs + flow[:, :, :, 0]
    y_dest = ys + flow[:, :, :, 1]
    src = im0.reshape(b, C, HW)
    dst = im1.view(b, C, HW)

    def scatter(yi, xi, weight, valid, add):
        idx = (yi * W + xi).view(b, 1, HW).expand(b, C, HW)
        if weight is None:
            vals = src * valid.view(b, 1, HW)
        else:
            vals = src * weight.view(b, 1, HW)
            vals.mul_(valid.view(b, 1, HW))
        if add:
            dst.scatter_add_(2, idx, vals)
        else:
            dst.scatter_(2, idx, vals)

    if interpolation_mode_int == 0:  # Bilinear
        x_f = torch.floor(x_dest)
        y_f = torch.floor(y_dest)
        ax = (x_f + 1) - x_dest     # x_ceil - x
        bx = x_dest - x_f
        ay = (y_f + 1) - y_dest
        by = y_dest - y_f
        del x_dest, y_dest
        xi = x_f.long()
        yi = y_f.long()
        del x_f, y_f
        # all four corners inside the image (the CUDA kernel's `if`)
        valid = ((xi >= 0) & (xi + 1 < W) & (yi >= 0) & (yi + 1 < H)).to(im0.dtype)
        x0 = xi.clamp(0, W - 1)
        x1 = (xi + 1).clamp_(0, W - 1)
        del xi
        y0 = yi.clamp(0, H - 1)
        y1 = (yi + 1).clamp_(0, H - 1)
        del yi
        scatter(y0, x0, ax * ay, valid, True)   # NW
        scatter(y0, x1, bx * ay, valid, True)   # NE
        scatter(y1, x0, ax * by, valid, True)   # SW
        scatter(y1, x1, bx * by, valid, True)   # SE
    else:  # Nearest: last write wins, like the CUDA kernel's `*im1_p = *im0_p`
        xn = torch.round(x_dest).long()
        yn = torch.round(y_dest).long()
        del x_dest, y_dest
        valid = ((xn >= 0) & (xn < W) & (yn >= 0) & (yn < H)).to(im0.dtype)
        scatter(yn.clamp_(0, H - 1), xn.clamp_(0, W - 1), None, valid, False)


class ForwardWarpFunction(Function):

    @staticmethod
    def forward(ctx, im0, flow, interpolation_mode_int):
        '''
        im0: the first image with shape [B, C, H, W]
        flow: the optical flow with shape [B, H, W, 2] (different to grid_sample, it's range is from [-W, -H] to [W, H])
        interpolation_mode_int: 0 for Bilinear, 1 for Nearest
        '''
        assert(len(im0.shape) == len(flow.shape) == 4)
        # Changed 'is' to '==' for integer comparison
        assert(interpolation_mode_int == 0 or interpolation_mode_int == 1)
        assert(im0.shape[0] == flow.shape[0])
        assert(im0.shape[-2:] == flow.shape[1:3])
        assert(flow.shape[3] == 2)
        assert(im0.is_contiguous())
        assert(flow.is_contiguous())
        # Assertions from original code for NaN/Inf are handled by PyTorch's default error checking
        # for floating point operations, and can be explicitly checked if absolutely necessary,
        # but typically not required for functional replacement.
        # assert(torch.isnan(flow).long().sum() == 0)
        # assert(torch.isinf(flow).long().sum() == 0)

        B, C, H, W = im0.shape
        im1 = torch.zeros_like(im0, device=im0.device, dtype=im0.dtype)
        # A few frames at a time, each corner's index once per pixel (not per
        # channel): the old flat [B*C*H*W] int64 indices made a 4K batch of 10
        # ask for ~14 GB and a 12 GB card ran out (tester, 2026-11-12). Same
        # sums in the same order, so the result is unchanged.
        step = max(1, _CHUNK_PIXELS // max(1, H * W))
        for b0 in range(0, B, step):
            b1 = min(B, b0 + step)
            _warp_chunk(im0[b0:b1], flow[b0:b1], im1[b0:b1], interpolation_mode_int)

        ctx.save_for_backward(im0, flow)
        ctx.interpolation_mode_int = interpolation_mode_int
        ctx.B, ctx.C, ctx.H, ctx.W = B, C, H, W
        
        return im1

    @staticmethod
    def backward(ctx, grad_output):
        im0, flow = ctx.saved_tensors
        interpolation_mode_int = ctx.interpolation_mode_int
        B, C, H, W = ctx.B, ctx.C, ctx.H, ctx.W

        grad_output = grad_output.contiguous()

        im0_grad = torch.zeros_like(im0, device=im0.device, dtype=im0.dtype)
        flow_grad = torch.zeros_like(flow, device=flow.device, dtype=flow.dtype)

        # Create a grid of pixel coordinates
        grid_x, grid_y = torch.meshgrid(
            torch.arange(W, device=im0.device, dtype=im0.dtype),
            torch.arange(H, device=im0.device, dtype=im0.dtype),
            indexing='xy'
        )
        grid_x = grid_x.unsqueeze(0).expand(B, -1, -1)
        grid_y = grid_y.unsqueeze(0).expand(B, -1, -1)

        x_dest = grid_x + flow[:, :, :, 0]
        y_dest = grid_y + flow[:, :, :, 1]

        if interpolation_mode_int == 0:  # Bilinear
            x_f = torch.floor(x_dest).long()
            y_f = torch.floor(y_dest).long()
            x_c = x_f + 1
            y_c = y_f + 1

            # Clamp coordinates to valid range [0, W-1] and [0, H-1]
            x_f_clamped = torch.clamp(x_f, 0, W - 1)
            y_f_clamped = torch.clamp(y_f, 0, H - 1)
            x_c_clamped = torch.clamp(x_c, 0, W - 1)
            y_c_clamped = torch.clamp(y_c, 0, H - 1)

            # Create mask for valid contributions in the forward pass.
            # This ensures gradients only flow back from locations that were actually written to.
            valid_mask_forward = (x_f >= 0) & (x_c < W) & (y_f >= 0) & (y_c < H)
            valid_mask_forward_bc = valid_mask_forward.unsqueeze(1).expand(-1, C, -1, -1) # [B, C, H, W]

            # Source pixel values needed for flow gradient calculation
            im0_bc = im0 # [B, C, H, W]

            # Get gradients at the 4 corners of the destination image
            # Base indices for batch and channel slices for flattened grad_output
            b_indices = torch.arange(B, device=im0.device).view(B, 1, 1, 1).expand(-1, C, H, W)
            c_indices = torch.arange(C, device=im0.device).view(1, C, 1, 1).expand(B, -1, H, W)
            base_idx_dest = b_indices * (C * H * W) + c_indices * (H * W)

            # Gather gradients from flattened grad_output at the 4 corner locations
            nw_grad = torch.gather(grad_output.view(-1), 0, (base_idx_dest + y_f_clamped.unsqueeze(1) * W + x_f_clamped.unsqueeze(1)).view(-1)).view(B,C,H,W)
            ne_grad = torch.gather(grad_output.view(-1), 0, (base_idx_dest + y_f_clamped.unsqueeze(1) * W + x_c_clamped.unsqueeze(1)).view(-1)).view(B,C,H,W)
            sw_grad = torch.gather(grad_output.view(-1), 0, (base_idx_dest + y_c_clamped.unsqueeze(1) * W + x_f_clamped.unsqueeze(1)).view(-1)).view(B,C,H,W)
            se_grad = torch.gather(grad_output.view(-1), 0, (base_idx_dest + y_c_clamped.unsqueeze(1) * W + x_c_clamped.unsqueeze(1)).view(-1)).view(B,C,H,W)

            # Apply the forward pass valid mask to the gathered gradients, zeroing out contributions that didn't happen
            nw_grad *= valid_mask_forward_bc.float()
            ne_grad *= valid_mask_forward_bc.float()
            sw_grad *= valid_mask_forward_bc.float()
            se_grad *= valid_mask_forward_bc.float()

            # Calculate weights (same as forward pass)
            nw_k = (x_c.float() - x_dest).unsqueeze(1)
            ne_k = (x_dest - x_f.float()).unsqueeze(1)
            sw_k = (x_c.float() - x_dest).unsqueeze(1)
            se_k = (x_dest - x_f.float()).unsqueeze(1)

            # Calculate im0_grad: sum of weighted gradients from output
            im0_grad += nw_k * nw_grad
            im0_grad += ne_k * ne_grad
            im0_grad += sw_k * sw_grad
            im0_grad += se_k * se_grad
            
            # Apply forward pass valid mask to im0_grad
            im0_grad *= valid_mask_forward_bc.float()

            # Calculate flow_grad_x and flow_grad_y by summing partial derivatives over channels
            # These partial derivatives are derived from chain rule:
            # d(Loss)/d(flow_x) = sum_c d(Loss)/d(im1_c) * d(im1_c)/d(x_dest) * d(x_dest)/d(flow_x)
            # Since d(x_dest)/d(flow_x) = 1, we focus on d(im1_c)/d(x_dest).
            # d(im1_c)/d(x_dest) = d(nw_k)/dx * p + d(ne_k)/dx * p + ...
            # The CUDA kernel combines these and sums.

            # Partial derivatives of weights with respect to x_dest and y_dest
            # d(nw_k)/dx = -(y_c - y_dest)
            # d(nw_k)/dy = -(x_c - x_dest)
            # d(ne_k)/dx = (y_c - y_dest)
            # d(ne_k)/dy = -(x_dest - x_f)
            # d(sw_k)/dx = -(y_dest - y_f)
            # d(sw_k)/dy = (x_c - x_dest)
            # d(se_k)/dx = (y_dest - y_f)
            # d(se_k)/dy = (x_dest - x_f)

            flow_grad_x_contrib = torch.zeros_like(im0_bc)
            flow_grad_y_contrib = torch.zeros_like(im0_bc)

            flow_grad_x_contrib -= (y_c.float().unsqueeze(1) - y_dest.unsqueeze(1)) * im0_bc * nw_grad
            flow_grad_x_contrib += (y_c.float().unsqueeze(1) - y_dest.unsqueeze(1)) * im0_bc * ne_grad
            flow_grad_x_contrib -= (y_dest.unsqueeze(1) - y_f.float().unsqueeze(1)) * im0_bc * sw_grad
            flow_grad_x_contrib += (y_dest.unsqueeze(1) - y_f.float().unsqueeze(1)) * im0_bc * se_grad

            flow_grad_y_contrib -= (x_c.float().unsqueeze(1) - x_dest.unsqueeze(1)) * im0_bc * nw_grad
            flow_grad_y_contrib -= (x_dest.unsqueeze(1) - x_f.float().unsqueeze(1)) * im0_bc * ne_grad
            flow_grad_y_contrib += (x_c.float().unsqueeze(1) - x_dest.unsqueeze(1)) * im0_bc * sw_grad
            flow_grad_y_contrib += (x_dest.unsqueeze(1) - x_f.float().unsqueeze(1)) * im0_bc * se_grad

            # Sum over the channel dimension to get final flow_grad [B, H, W, 2]
            flow_grad[:, :, :, 0] = flow_grad_x_contrib.sum(dim=1)
            flow_grad[:, :, :, 1] = flow_grad_y_contrib.sum(dim=1)
            
            # Apply forward pass valid mask to flow_grad
            flow_grad *= valid_mask_forward.unsqueeze(-1).float()

        else:  # Nearest
            x_nearest = torch.round(x_dest).long()
            y_nearest = torch.round(y_dest).long()

            valid_mask_forward = (x_nearest >= 0) & (x_nearest < W) & \
                                 (y_nearest >= 0) & (y_nearest < H)
            valid_mask_forward_bc = valid_mask_forward.unsqueeze(1).expand(-1, C, -1, -1)

            # In nearest neighbor, gradient flows back directly.
            # Gather `grad_output` values based on where `im0` pixels would have gone.
            b_indices = torch.arange(B, device=im0.device).view(B, 1, 1, 1).expand(-1, C, H, W)
            c_indices = torch.arange(C, device=im0.device).view(1, C, 1, 1).expand(B, -1, H, W)
            base_idx_dest = b_indices * (C * H * W) + c_indices * (H * W)

            dest_flat_idx = base_idx_dest + \
                            y_nearest.unsqueeze(1) * W + \
                            x_nearest.unsqueeze(1)
            
            # Gather gradients from `grad_output` at the destination points
            im0_grad_gathered = torch.gather(grad_output.view(-1), 0, dest_flat_idx.view(-1)).view(B,C,H,W)
            im0_grad = im0_grad_gathered * valid_mask_forward_bc.float()
            
            # Flow gradient for Nearest is zero unless explicitly defined.
            # The CUDA `forward_warp_cuda_backward_kernel` for Nearest also implicitly sets flow_grad to zero.
            # So, flow_grad remains as its initial zeros_like value.

        return im0_grad, flow_grad, None # None for interpolation_mode_int gradient


class forward_warp(Module):

    def __init__(self, interpolation_mode="Bilinear"):
        '''
        Support interpolation mode with Bilinear and Nearest.
        '''
        super(forward_warp, self).__init__()
        assert(interpolation_mode in ["Bilinear", "Nearest"])
        # Changed 'is' to '==' for integer comparison
        if(interpolation_mode == "Bilinear"):
            self.interpolation_mode_int = 0
        else:
            self.interpolation_mode_int = 1

    def forward(self, im0, flow):

        return ForwardWarpFunction.apply(im0, flow, self.interpolation_mode_int)