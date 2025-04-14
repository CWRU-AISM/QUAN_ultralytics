# Ultralytics 🚀 AGPL-3.0 License - https://ultralytics.com/license
"""Convolution modules."""

import math

import numpy as np
import torch
import torch.nn as nn
from typing import Union, Tuple
import torch.nn.functional as F
from .activation import *
from torch.jit import script
from torch.utils import cpp_extension

__all__ = (
    "Conv",
    "Conv2",
    "QConv",
    "QConv2D",
    "QConcat",
    "QUpsample",
    "IQBN",
    "IQLN"
    "LightConv",
    "DWConv",
    "DWConvTranspose2d",
    "ConvTranspose",
    "Focus",
    "GhostConv",
    "ChannelAttention",
    "SpatialAttention",
    "CBAM",
    "QConcat",
    "Concat",
    "RepConv",
    "Index",
    "QUpsample"
)


def autopad(k, p=None, d=1):  # kernel, padding, dilation
    """Pad to 'same' shape outputs."""
    if d > 1:
        k = d * (k - 1) + 1 if isinstance(k, int) else [d * (x - 1) + 1 for x in k]  # actual kernel-size
    if p is None:
        p = k // 2 if isinstance(k, int) else [x // 2 for x in k]  # auto-pad
    return p

# def autopad(k, p=None, d=1):  # kernel, padding, dilation
#     """Pad to 'same' shape outputs. Expects d as int."""
#     if d > 1:
#         # Calculate actual kernel size considering dilation
#         # This part assumes d is an integer.
#         k_eff = d * (k - 1) + 1 if isinstance(k, int) else [d * (x - 1) + 1 for x in k]
#     else:
#         k_eff = k # If d=1, effective kernel size is the same as k

#     # Auto-pad based on the effective kernel size
#     if p is None:
#         p = k_eff // 2 if isinstance(k_eff, int) else [x // 2 for x in k_eff]
#     return p

# class IQBN(nn.Module):
#     def __init__(self, num_features, eps=1e-5, momentum=0.1):
#         super().__init__()
#         # Standard BatchNorm with 4x channels (one for each quaternion component)
#         self.num_features = num_features
#         self.eps = eps
#         self.momentum = momentum
#         # self.weight = torch.zeros(num_features)
#         self.bn = nn.BatchNorm2d(self.num_features, self.eps, self.momentum)       
#     def forward(self, x):
#         # x shape: [B, C, 4, H, W]
#         B, C, Q, H, W = x.shape
#         # Reshape to [B, C*4, H, W]
#         x_reshaped = x.reshape(B, C*Q, H, W)
#         # Apply batch norm
#         x_bn = self.bn(x_reshaped)
#         # Reshape back to [B, C, 4, H, W]
#         return x_bn.reshape(B, C, Q, H, W)

# class IQBN(nn.Module):
#     def __init__(self, num_features, eps=1e-5, momentum=0.1):
#         super().__init__()
#         self.num_features = num_features // 4
#         self.eps = eps
#         self.momentum = momentum
        
#         # Store parameters efficiently
#         self.gamma = nn.Parameter(torch.ones(1, self.num_features, 4, 1, 1))
#         self.beta = nn.Parameter(torch.zeros(1, self.num_features, 4, 1, 1))
        
#         # Running stats already in broadcast-ready shape
#         self.register_buffer('running_mean', torch.zeros(1, self.num_features, 4, 1, 1))
#         self.register_buffer('running_var', torch.ones(1, self.num_features, 4, 1, 1))

#     def forward(self, x):
#         if not self.training:
#             # Fast inference path - direct operations with no reshaping
#             return x * self.gamma.to(x.dtype) * torch.rsqrt(self.running_var + self.eps) + (self.beta - self.running_mean * self.gamma * torch.rsqrt(self.running_var + self.eps))
        
#         # Training path - optimize for speed
#         B, C, Q, H, W = x.shape
        
#         # Flatten once
#         x_flat = x.reshape(B, C, Q, H*W)
        
#         # Fast statistics calculation
#         mean = torch.mean(x_flat, dim=3, keepdim=True).mean(dim=0, keepdim=True)  # [1, C, Q, 1]
#         # Center the data once
#         x_centered = x_flat - mean
#         # Compute variance efficiently
#         var = torch.mean(x_centered**2, dim=3, keepdim=True).mean(dim=0, keepdim=True)  # [1, C, Q, 1]
        
#         # Reshape means and vars only once
#         mean_reshaped = mean.reshape(1, self.num_features, 4, 1, 1)
#         var_reshaped = var.reshape(1, self.num_features, 4, 1, 1)
        
#         # Update running stats (minimal operations)
#         if self.momentum != 0.0:
#             with torch.no_grad():
#                 self.running_mean.mul_(1 - self.momentum).add_(mean_reshaped * self.momentum)
#                 self.running_var.mul_(1 - self.momentum).add_(var_reshaped * self.momentum)
        
#         # Compute inverse standard deviation once
#         inv_std = torch.rsqrt(var_reshaped + self.eps)
        
#         # Reshape input back to original shape
#         x = x.reshape(B, C, Q, H, W)
        
#         # Apply normalization in most efficient order (minimize intermediate results)
#         return (x - mean_reshaped) * inv_std * self.gamma + self.beta

class IQBN(nn.Module):
    def __init__(self, num_features, eps=1e-5, momentum=0.1):
        super().__init__()
        self.num_features = num_features // 4
        self.eps = eps
        self.momentum = momentum
        
        # Parameters for correct broadcasting
        self.gamma = nn.Parameter(torch.ones(self.num_features, 4))
        self.beta = nn.Parameter(torch.zeros(self.num_features, 4))
        
        # Running stats with correct shapes
        self.register_buffer('running_mean', torch.zeros(self.num_features, 4))
        self.register_buffer('running_var', torch.ones(self.num_features, 4))

    def forward(self, x):
        # Quick shape check
        B, C, Q, H, W = x.shape
        
        if not self.training:
            # Faster evaluation using pre-computed stats
            mean = self.running_mean.view(1, self.num_features, 4, 1, 1)
            var = self.running_var.view(1, self.num_features, 4, 1, 1)
            x_norm = (x - mean) / torch.sqrt(var + self.eps)
            return x_norm * self.gamma.view(1, self.num_features, 4, 1, 1) + self.beta.view(1, self.num_features, 4, 1, 1)
        
        # Training mode optimized
        # Batch statistics - process all spatial dimensions at once for efficiency
        x_flat = x.reshape(B, C, Q, -1)
        mean = x_flat.mean(dim=[0, 3], keepdim=True)  # [1, C, Q, 1]
        var = x_flat.var(dim=[0, 3], keepdim=True, unbiased=False) + 1e-8  # Added eps for stability
        
        # Update running stats
        with torch.no_grad():
            self.running_mean = (1 - self.momentum) * self.running_mean + self.momentum * mean.squeeze()
            self.running_var = (1 - self.momentum) * self.running_var + self.momentum * var.squeeze()
        
        # Normalize
        x_norm = (x - mean.view(1, C, Q, 1, 1)) / torch.sqrt(var.view(1, C, Q, 1, 1) + self.eps)
        
        # Apply affine parameters
        return x_norm * self.gamma.view(1, self.num_features, 4, 1, 1) + self.beta.view(1, self.num_features, 4, 1, 1)
    
class Conv(nn.Module):
    default_act = nn.SiLU()  # default activation

    def __init__(self, c1, c2, k=1, s=1, p=None, g=1, d=1, act=True):
        """Initialize Conv layer with given arguments including activation."""
        super().__init__()
        padding = autopad(k, p, d)

        self.conv = QConv2D(c1, c2, k, s, padding, groups=g, dilation=d, bias=False)
        self.bn = IQBN(c2)
        self.act = self.default_act if act is True else act if isinstance(act, nn.Module) else nn.Identity()
        # self.conv.stride = s if isinstance(s, tuple) else (s, s)
        # self.conv.padding = padding if isinstance(padding, tuple) else (padding, padding)

        # self.conv.dilation = d if isinstance(d, tuple) else (d, d)
        # self.conv.groups = g

    def forward(self, x):
        """Apply convolution, batch normalization and activation to input tensor."""
        # print(f"X type: {x.dtype}")
        return self.act(self.bn(self.conv(x)))

    def forward_fuse(self, x):
        """Apply convolution and activation without batch normalization."""
        return self.act(self.conv(x))



class QConv(nn.Module):
    """
    Base Quaternion Convolution class.
    """
    def __init__(self, 
                 rank: int,
                 in_channels: int,
                 out_channels: int,
                 kernel_size: Union[int, Tuple[int, ...]],
                 stride: Union[int, Tuple[int, ...]] = 1,
                 padding: Union[str, int, Tuple[int, ...]] = 0,
                 dilation: Union[int, Tuple[int, ...]] = 1,
                 groups: int = 1,
                 bias: bool = True,
                 padding_mode: str = 'zeros',
                 dtype=None,
                 mapping_type: str = 'poincare') -> None:
        super(QConv, self).__init__()
        

        assert rank in [1, 2, 3], "rank must be 1, 2, or 3"
        
        valid_mappings = ['luminance', 'mean_brightness', 'raw_normalized', 'hamilton', 'poincare']
        assert mapping_type in valid_mappings, f"Invalid mapping type. Choose from {valid_mappings}"
        
        self.mapping_type = mapping_type
        # Special handling for first layer

        self.rank = rank
        self.in_channels = in_channels
        self.out_channels = out_channels
        self.groups = groups
        self.kernel_size = kernel_size if isinstance(kernel_size, tuple) else (kernel_size,) * rank
        
        self.mapping_type = mapping_type
        # Define the underlying real-valued convolution for each quaternion component
        if rank == 1:
            Conv = nn.Conv1d
        elif rank == 2:
            Conv = nn.Conv2d
        else:
            Conv = nn.Conv3d
            
        self.is_first_layer = (in_channels == 3)  # Changed from 4 to 3
        if self.is_first_layer:
            # For RGB input, map to 4 channels
            actual_in_channels = 1  # Use this for the convolution
        else:
            assert in_channels % 4 == 0, "in_channels must be multiple of 4 for non-first layers"
            actual_in_channels = in_channels // 4
        assert out_channels % 4 == 0, "out_channels must be multiple of 4"
        
        # For first layer, use in_channels=1, for others use in_channels//4
        out_channels_quat = out_channels // 4
        
        self.conv_r = Conv(actual_in_channels, out_channels_quat, kernel_size,
                          stride, padding, dilation, groups, bias, 
                          padding_mode)
        
        self.conv_i = Conv(actual_in_channels, out_channels_quat, kernel_size,
                          stride, padding, dilation, groups, False, 
                          padding_mode)
        
        self.conv_j = Conv(actual_in_channels, out_channels_quat, kernel_size,
                          stride, padding, dilation, groups, False, 
                          padding_mode)
        
        self.conv_k = Conv(actual_in_channels, out_channels_quat, kernel_size,
                          stride, padding, dilation, groups, False, 
                          padding_mode)
                      
        self._initialize_weights()


    # Bias for all layers weight init
    def _initialize_weights(self):
        
        kernel_prod = np.prod(self.kernel_size)
        fan_in = (self.in_channels // 4 if not self.is_first_layer else 1) * kernel_prod
        
        # Scale factors for quaternion components
        scale_factors = {
            'luminance': [1.0, 1.0, 1.0, 1.0],      # Emphasize real component
            'mean_brightness': [1.0, 0.75, 0.75, 0.75],  # Slightly more balanced
            'raw_normalized': [1.0, 1.0, 1.0, 1.0],  # Equal emphasis
            'poincare': [1.0, 1.0, 1.0, 1.0]  # Equal emphasis

        }
        scales = scale_factors.get(self.mapping_type, [0.5, 0.5, 0.5, 0.5])
        
        # All convolution layers
        convs = [self.conv_r, self.conv_i, self.conv_j, self.conv_k]
        
        for i, conv in enumerate(convs):
            # Weight initialization with scaled Kaiming
            nn.init.kaiming_uniform_(conv.weight, a=math.sqrt(5) * scales[i])
            
            # Bias initialization (if present)
            if conv.bias is not None:
                bound = 1 / math.sqrt(fan_in) * scales[i]  # Scale bias bound by component weight
                nn.init.uniform_(conv.bias, -bound, bound)


            # No bias for i, j, k components
    def forward(self, x: torch.Tensor) -> torch.Tensor:
        # Handle RGB input
        if x.size(1) == 3:  # RGB input
            x = self.rgb_to_quaternion(x)
            
        if self.is_first_layer:
            # Process first layer more efficiently
            B, Q, H, W = x.shape
            # Stack components for single batch processing
            x_stacked = x.reshape(B*Q, 1, H, W)
            r_conv = self.conv_r(x_stacked.view(B, Q, H, W)[:, 0:1])
            i_conv = self.conv_i(x_stacked.view(B, Q, H, W)[:, 1:2])
            j_conv = self.conv_j(x_stacked.view(B, Q, H, W)[:, 2:3])
            k_conv = self.conv_k(x_stacked.view(B, Q, H, W)[:, 3:4])
        else:
            # For subsequent layers, use channel-wise processing
            x_r = x[:, :, 0, :, :]
            x_i = x[:, :, 1, :, :]
            x_j = x[:, :, 2, :, :]
            x_k = x[:, :, 3, :, :]
            
            # Process in parallel if possible
            r_conv = self.conv_r(x_r)
            i_conv = self.conv_i(x_i)
            j_conv = self.conv_j(x_j)
            k_conv = self.conv_k(x_k)
        
        # Use in-place operations and fuse calculations where possible
        out_r = r_conv - i_conv - j_conv - k_conv  # No in-place for first op
        
        out_i = r_conv.clone() 
        out_i.add_(i_conv).add_(j_conv).sub_(k_conv)  # Chain in-place ops
        
        out_j = r_conv.clone()
        out_j.sub_(i_conv).add_(j_conv).add_(k_conv)
        
        out_k = r_conv.clone()
        out_k.add_(i_conv).sub_(j_conv).add_(k_conv)
        
        # Stack outputs efficiently
        return torch.stack([out_r, out_i, out_j, out_k], dim=2)
        

    def rgb_to_quaternion(self, rgb_input):
        B, C, H, W = rgb_input.shape
        luminance = (0.299 * rgb_input[:, 0] + 0.587 * rgb_input[:, 1] + 0.114 * rgb_input[:, 2]).unsqueeze(1).to(rgb_input.device)
        mean_brightness = rgb_input.mean(dim=1, keepdim=True).to(rgb_input.device)
        rgb_normalized = ((rgb_input - rgb_input.min()) / (rgb_input.max() - rgb_input.min())).to(rgb_input.device)
        
        def hamilton_mapping(x):
            real = torch.zeros_like(x[:, 0:1])
            return torch.cat([real, x[:, 0:1], x[:, 1:2], x[:, 2:3]], dim=1)
        
        def poincare_mapping(x):
            norm = torch.norm(x, dim=1, keepdim=True)
            x_normalized = (x / (norm + 1))
            return torch.cat([torch.sqrt(1 - torch.sum(x_normalized**2, dim=1, keepdim=True)), 
                            x_normalized[:, 0:1], x_normalized[:, 1:2], x_normalized[:, 2:3]], dim=1)
        
        mappings = {
            'luminance': torch.cat([luminance, rgb_normalized[:, 0:1], rgb_normalized[:, 1:2], rgb_normalized[:, 2:3]], dim=1),
            'mean_brightness': torch.cat([mean_brightness, rgb_input[:, 0:1], rgb_input[:, 1:2], rgb_input[:, 2:3]], dim=1),
            'raw_normalized': torch.cat([rgb_normalized.mean(dim=1, keepdim=True), 
                                        rgb_normalized[:, 0:1], rgb_normalized[:, 1:2], rgb_normalized[:, 2:3]], dim=1),
            'hamilton': hamilton_mapping(rgb_input),
            'poincare': poincare_mapping(rgb_input)
        }
        return mappings[self.mapping_type]
    
class QConv2D(QConv):
    """2D Quaternion Convolution layer."""
    def __init__(self,
                 in_channels: int,
                 out_channels: int,
                 kernel_size: Union[int, Tuple[int, int]],
                 stride: Union[int, Tuple[int, int]] = 1,
                 padding: Union[str, int, Tuple[int, int]] = 0,
                 dilation: Union[int, Tuple[int, int]] = 1,
                 groups: int = 1,
                 bias: bool = True,
                 padding_mode: str = 'zeros',
                 dtype=None,
                 mapping_type: str='poincare') -> None:
        super().__init__(
            rank=2,  # Fixed for 2D convolution
            in_channels=in_channels,
            out_channels=out_channels,
            kernel_size=kernel_size,
            stride=stride,
            padding=padding,
            dilation=dilation,
            groups=groups,
            bias=bias,
            padding_mode=padding_mode,
            dtype=dtype,
            mapping_type=mapping_type
        )

    # @property
    # def weight(self):
    #     """Compatibility property for functions expecting standard Conv2d weight."""
    #     # Create a weight tensor that combines the quaternion kernels
    #     # This is just for compatibility, not for actual computation
    #     dummy_weight = torch.zeros(
    #         (self.out_channels, self.in_channels // self.groups, *self.kernel_size)
    #     )
    #     return dummy_weight
# class Conv(nn.Module):
#     """Standard convolution with args(ch_in, ch_out, kernel, stride, padding, groups, dilation, activation)."""

#     default_act = nn.SiLU()  # default activation

#     def __init__(self, c1, c2, k=1, s=1, p=None, g=1, d=1, act=True):
#         """Initialize Conv layer with given arguments including activation."""
#         super().__init__()
#         self.conv = nn.Conv2d(c1, c2, k, s, autopad(k, p, d), groups=g, dilation=d, bias=False)
#         self.bn = nn.BatchNorm2d(c2)
#         self.act = self.default_act if act is True else act if isinstance(act, nn.Module) else nn.Identity()

#     def forward(self, x):
#         """Apply convolution, batch normalization and activation to input tensor."""
#         return self.act(self.bn(self.conv(x)))

#     def forward_fuse(self, x):
#         """Apply convolution and activation without batch normalization."""
#         return self.act(self.conv(x))




class Conv2(Conv):
    """Simplified RepConv module with Conv fusing."""

    def __init__(self, c1, c2, k=3, s=1, p=None, g=1, d=1, act=True):
        """Initialize Conv layer with given arguments including activation."""
        super().__init__(c1, c2, k, s, p, g=g, d=d, act=act)
        self.cv2 = nn.Conv2d(c1, c2, 1, s, autopad(1, p, d), groups=g, dilation=d, bias=False)  # add 1x1 conv

    def forward(self, x):
        """Apply convolution, batch normalization and activation to input tensor."""
        return self.act(self.bn(self.conv(x) + self.cv2(x)))

    def forward_fuse(self, x):
        """Apply fused convolution, batch normalization and activation to input tensor."""
        return self.act(self.bn(self.conv(x)))

    def fuse_convs(self):
        """Fuse parallel convolutions."""
        w = torch.zeros_like(self.conv.weight.data)
        i = [x // 2 for x in w.shape[2:]]
        w[:, :, i[0] : i[0] + 1, i[1] : i[1] + 1] = self.cv2.weight.data.clone()
        self.conv.weight.data += w
        self.__delattr__("cv2")
        self.forward = self.forward_fuse


class LightConv(nn.Module):
    """
    Light convolution with args(ch_in, ch_out, kernel).

    https://github.com/PaddlePaddle/PaddleDetection/blob/develop/ppdet/modeling/backbones/hgnet_v2.py
    """

    def __init__(self, c1, c2, k=1, act=nn.ReLU()):
        """Initialize Conv layer with given arguments including activation."""
        super().__init__()
        self.conv1 = Conv(c1, c2, 1, act=False)
        self.conv2 = DWConv(c2, c2, k, act=act)

    def forward(self, x):
        """Apply 2 convolutions to input tensor."""
        return self.conv2(self.conv1(x))


class DWConv(Conv):
    """Depth-wise convolution."""

    def __init__(self, c1, c2, k=1, s=1, d=1, act=True):  # ch_in, ch_out, kernel, stride, dilation, activation
        """Initialize Depth-wise convolution with given parameters."""
        super().__init__(c1, c2, k, s, g=math.gcd(c1//4, c2//4), d=d, act=act)


class DWConvTranspose2d(nn.ConvTranspose2d):
    """Depth-wise transpose convolution."""

    def __init__(self, c1, c2, k=1, s=1, p1=0, p2=0):  # ch_in, ch_out, kernel, stride, padding, padding_out
        """Initialize DWConvTranspose2d class with given parameters."""
        super().__init__(c1, c2, k, s, p1, p2, groups=math.gcd(c1, c2))


class ConvTranspose(nn.Module):
    """Convolution transpose 2d layer."""

    default_act = nn.SiLU()  # default activation

    def __init__(self, c1, c2, k=2, s=2, p=0, bn=True, act=True):
        """Initialize ConvTranspose2d layer with batch normalization and activation function."""
        super().__init__()
        self.conv_transpose = nn.ConvTranspose2d(c1, c2, k, s, p, bias=not bn)
        self.bn = nn.BatchNorm2d(c2) if bn else nn.Identity()
        self.act = self.default_act if act is True else act if isinstance(act, nn.Module) else nn.Identity()

    def forward(self, x):
        """Applies transposed convolutions, batch normalization and activation to input."""
        return self.act(self.bn(self.conv_transpose(x)))

    def forward_fuse(self, x):
        """Applies activation and convolution transpose operation to input."""
        return self.act(self.conv_transpose(x))


class Focus(nn.Module):
    """Focus wh information into c-space."""

    def __init__(self, c1, c2, k=1, s=1, p=None, g=1, act=True):
        """Initializes Focus object with user defined channel, convolution, padding, group and activation values."""
        super().__init__()
        self.conv = Conv(c1 * 4, c2, k, s, p, g, act=act)
        # self.contract = Contract(gain=2)

    def forward(self, x):
        """
        Applies convolution to concatenated tensor and returns the output.

        Input shape is (b,c,w,h) and output shape is (b,4c,w/2,h/2).
        """
        return self.conv(torch.cat((x[..., ::2, ::2], x[..., 1::2, ::2], x[..., ::2, 1::2], x[..., 1::2, 1::2]), 1))
        # return self.conv(self.contract(x))


class GhostConv(nn.Module):
    """Ghost Convolution https://github.com/huawei-noah/ghostnet."""

    def __init__(self, c1, c2, k=1, s=1, g=1, act=True):
        """Initializes Ghost Convolution module with primary and cheap operations for efficient feature learning."""
        super().__init__()
        c_ = c2 // 2  # hidden channels
        self.cv1 = Conv(c1, c_, k, s, None, g, act=act)
        self.cv2 = Conv(c_, c_, 5, 1, None, c_, act=act)

    def forward(self, x):
        """Forward propagation through a Ghost Bottleneck layer with skip connection."""
        y = self.cv1(x)
        return torch.cat((y, self.cv2(y)), 1)


class RepConv(nn.Module):
    """
    RepConv is a basic rep-style block, including training and deploy status.

    This module is used in RT-DETR.
    Based on https://github.com/DingXiaoH/RepVGG/blob/main/repvgg.py
    """

    default_act = nn.SiLU()  # default activation

    def __init__(self, c1, c2, k=3, s=1, p=1, g=1, d=1, act=True, bn=False, deploy=False):
        """Initializes Light Convolution layer with inputs, outputs & optional activation function."""
        super().__init__()
        assert k == 3 and p == 1
        self.g = g
        self.c1 = c1
        self.c2 = c2
        self.act = self.default_act if act is True else act if isinstance(act, nn.Module) else nn.Identity()

        self.bn = nn.BatchNorm2d(num_features=c1) if bn and c2 == c1 and s == 1 else None
        self.conv1 = Conv(c1, c2, k, s, p=p, g=g, act=False)
        self.conv2 = Conv(c1, c2, 1, s, p=(p - k // 2), g=g, act=False)

    def forward_fuse(self, x):
        """Forward process."""
        return self.act(self.conv(x))

    def forward(self, x):
        """Forward process."""
        id_out = 0 if self.bn is None else self.bn(x)
        return self.act(self.conv1(x) + self.conv2(x) + id_out)

    def get_equivalent_kernel_bias(self):
        """Returns equivalent kernel and bias by adding 3x3 kernel, 1x1 kernel and identity kernel with their biases."""
        kernel3x3, bias3x3 = self._fuse_bn_tensor(self.conv1)
        kernel1x1, bias1x1 = self._fuse_bn_tensor(self.conv2)
        kernelid, biasid = self._fuse_bn_tensor(self.bn)
        return kernel3x3 + self._pad_1x1_to_3x3_tensor(kernel1x1) + kernelid, bias3x3 + bias1x1 + biasid

    @staticmethod
    def _pad_1x1_to_3x3_tensor(kernel1x1):
        """Pads a 1x1 tensor to a 3x3 tensor."""
        if kernel1x1 is None:
            return 0
        else:
            return torch.nn.functional.pad(kernel1x1, [1, 1, 1, 1])

    def _fuse_bn_tensor(self, branch):
        """Generates appropriate kernels and biases for convolution by fusing branches of the neural network."""
        if branch is None:
            return 0, 0
        if isinstance(branch, Conv):
            kernel = branch.conv.weight
            running_mean = branch.bn.running_mean
            running_var = branch.bn.running_var
            gamma = branch.bn.weight
            beta = branch.bn.bias
            eps = branch.bn.eps
        elif isinstance(branch, nn.BatchNorm2d):
            if not hasattr(self, "id_tensor"):
                input_dim = self.c1 // self.g
                kernel_value = np.zeros((self.c1, input_dim, 3, 3), dtype=np.float32)
                for i in range(self.c1):
                    kernel_value[i, i % input_dim, 1, 1] = 1
                self.id_tensor = torch.from_numpy(kernel_value).to(branch.weight.device)
            kernel = self.id_tensor
            running_mean = branch.running_mean
            running_var = branch.running_var
            gamma = branch.weight
            beta = branch.bias
            eps = branch.eps
        std = (running_var + eps).sqrt()
        t = (gamma / std).reshape(-1, 1, 1, 1)
        return kernel * t, beta - running_mean * gamma / std

    def fuse_convs(self):
        """Combines two convolution layers into a single layer and removes unused attributes from the class."""
        if hasattr(self, "conv"):
            return
        kernel, bias = self.get_equivalent_kernel_bias()
        self.conv = nn.Conv2d(
            in_channels=self.conv1.conv.in_channels,
            out_channels=self.conv1.conv.out_channels,
            kernel_size=self.conv1.conv.kernel_size,
            stride=self.conv1.conv.stride,
            padding=self.conv1.conv.padding,
            dilation=self.conv1.conv.dilation,
            groups=self.conv1.conv.groups,
            bias=True,
        ).requires_grad_(False)
        self.conv.weight.data = kernel
        self.conv.bias.data = bias
        for para in self.parameters():
            para.detach_()
        self.__delattr__("conv1")
        self.__delattr__("conv2")
        if hasattr(self, "nm"):
            self.__delattr__("nm")
        if hasattr(self, "bn"):
            self.__delattr__("bn")
        if hasattr(self, "id_tensor"):
            self.__delattr__("id_tensor")


class ChannelAttention(nn.Module):
    """Channel-attention module https://github.com/open-mmlab/mmdetection/tree/v3.0.0rc1/configs/rtmdet."""

    def __init__(self, channels: int) -> None:
        """Initializes the class and sets the basic configurations and instance variables required."""
        super().__init__()
        self.pool = nn.AdaptiveAvgPool2d(1)
        self.fc = nn.Conv2d(channels, channels, 1, 1, 0, bias=True)
        self.act = nn.Sigmoid()

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        """Applies forward pass using activation on convolutions of the input, optionally using batch normalization."""
        return x * self.act(self.fc(self.pool(x)))


class SpatialAttention(nn.Module):
    """Spatial-attention module."""

    def __init__(self, kernel_size=7):
        """Initialize Spatial-attention module with kernel size argument."""
        super().__init__()
        assert kernel_size in {3, 7}, "kernel size must be 3 or 7"
        padding = 3 if kernel_size == 7 else 1
        self.cv1 = nn.Conv2d(2, 1, kernel_size, padding=padding, bias=False)
        self.act = nn.Sigmoid()

    def forward(self, x):
        """Apply channel and spatial attention on input for feature recalibration."""
        return x * self.act(self.cv1(torch.cat([torch.mean(x, 1, keepdim=True), torch.max(x, 1, keepdim=True)[0]], 1)))


class CBAM(nn.Module):
    """Convolutional Block Attention Module."""

    def __init__(self, c1, kernel_size=7):
        """Initialize CBAM with given input channel (c1) and kernel size."""
        super().__init__()
        self.channel_attention = ChannelAttention(c1)
        self.spatial_attention = SpatialAttention(kernel_size)

    def forward(self, x):
        """Applies the forward pass through C1 module."""
        return self.spatial_attention(self.channel_attention(x))


class Concat(nn.Module):
    """Concatenate a list of tensors along dimension."""

    def __init__(self, dimension=1):
        """Concatenates a list of tensors along a specified dimension."""
        super().__init__()
        self.d = dimension

    def forward(self, x):
        """Forward pass for the YOLOv8 mask Proto module."""
        return torch.cat(x, self.d)


class Index(nn.Module):
    """Returns a particular index of the input."""

    def __init__(self, index=0):
        """Returns a particular index of the input."""
        super().__init__()
        self.index = index

    def forward(self, x):
        """
        Forward pass.

        Expects a list of tensors as input.
        """
        return x[self.index]



class QConcat(nn.Module):
    def __init__(self, dim=1, reduce=False, target_channels=None):
        super().__init__()
        self.dim = dim
        self.reduce = reduce
        self.target_channels = target_channels
        
        if reduce:
            assert target_channels is not None, "target_channels must be specified when reduce=True"
            assert target_channels % 4 == 0, "target_channels must be multiple of 4"
            # Create single quaternion convolution to reduce channels
            self.reduce_conv = QConv2D(target_channels * 4, target_channels, kernel_size=1)

    def forward(self, x: list) -> torch.Tensor:
        """
        Args:
            x: List of quaternion tensors each of shape [B, C, 4, H, W]
        Returns:
            torch.Tensor: Concatenated tensor [B, C', 4, H, W]
        """
        # Verify all inputs have quaternion structure
        assert all(tensor.size(2) == 4 for tensor in x), "All inputs must have quaternion dimension"
        
        # Concatenate along channel dimension while preserving quaternion structure
        concat = torch.cat(x, dim=1)  # [B, sum(C), 4, H, W]
        
        # Reduce channels if needed
        if self.reduce:
            concat = self.reduce_conv(concat)
            
        return concat

class QUpsample(nn.Module):
    def __init__(self, scale_factor=2, mode='nearest'):
        super().__init__()
        self.scale_factor = scale_factor
        self.mode = mode
    
    def forward(self, x):
        B, C, Q, H, W = x.shape
        
        # Reshape to handle quaternion components separately
        x = x.permute(0, 2, 1, 3, 4).reshape(B*Q, C, H, W)
        
        # Upsample
        x = F.interpolate(x, scale_factor=self.scale_factor, mode=self.mode)
        
        # Reshape back to quaternion format
        _, _, H_new, W_new = x.shape
        x = x.reshape(B, Q, C, H_new, W_new).permute(0, 2, 1, 3, 4)
        
        return x

# class DWConv(nn.Module):
#     """Quaternion Depthwise Convolution (Simplified & Corrected)."""
#     default_act = nn.SiLU()

#     def __init__(self, c1, c2, k=3, s=1, d=1, act=True):
#         super().__init__()
#         # DWConv requires input channels == output channels
#         if c1 != c2:
#             # This might be okay if subsequent layers handle it, but unusual for pure DWConv
#             # print(f"Warning: DWConv initialized with c1={c1}, c2={c2}. Standard DWConv has c1=c2.")
#             # Let's proceed assuming the definition allows c1!=c2, but apply depthwise based on c1
#              pass # Allow c1 != c2 based on usage in head (DW(x,x) then Conv(x,c3))


#         if c1 % 4 != 0:
#             raise ValueError(f"DWConv input channels c1={c1} must be a multiple of 4.")
#         if c2 % 4 != 0:
#             raise ValueError(f"DWConv output channels c2={c2} must be a multiple of 4.")

#         qc1 = c1 // 4 # Input channels per component
#         qc2 = c2 // 4 # Output channels per component

#         # Key: groups = qc1 makes it depthwise *relative to the input components*
#         # Each input component channel gets its own filter kernel.
#         groups = qc1
#         padding = autopad(k, None, d)

#         # Define the 4 component-wise convolutions. They are depthwise w.r.t qc1.
#         # Output channels are qc2. If c1=c2, then qc1=qc2.
#         self.conv_r = nn.Conv2d(qc1, qc2, k, s, padding, groups=groups, dilation=d, bias=False)
#         self.conv_i = nn.Conv2d(qc1, qc2, k, s, padding, groups=groups, dilation=d, bias=False)
#         self.conv_j = nn.Conv2d(qc1, qc2, k, s, padding, groups=groups, dilation=d, bias=False)
#         self.conv_k = nn.Conv2d(qc1, qc2, k, s, padding, groups=groups, dilation=d, bias=False)

#         # Use the efficient IQBN - applies normalization across the final C2 channels
#         self.bn = IQBN(c2)
#         self.act = self.default_act if act is True else act if isinstance(act, nn.Module) else nn.Identity()

#         # Store attributes for potential use/inspection
#         self.stride = s
#         self.padding = padding
#         self.kernel_size = k
#         self.dilation = d
#         self.groups = groups # Note: groups *per component convolution*

#     def forward(self, x):
#         # Input x expected shape: [B, qc1, 4, H, W]
#         x_r = x[:, :, 0, :, :] # Shape: [B, qc1, H, W]
#         x_i = x[:, :, 1, :, :] # Shape: [B, qc1, H, W]
#         x_j = x[:, :, 2, :, :] # Shape: [B, qc1, H, W]
#         x_k = x[:, :, 3, :, :] # Shape: [B, qc1, H, W]

#         # Apply component-wise depthwise conv based on input qc1
#         # Output shapes: [B, qc2, H', W']
#         # Hamilton product structure requires applying the kernel components (conv_r..k)
#         # to the input components (x_r..k) correctly.
#         final_r = self.conv_r(x_r) - self.conv_i(x_i) - self.conv_j(x_j) - self.conv_k(x_k)
#         final_i = self.conv_r(x_i) + self.conv_i(x_r) + self.conv_j(x_k) - self.conv_k(x_j)
#         final_j = self.conv_r(x_j) - self.conv_i(x_k) + self.conv_j(x_r) + self.conv_k(x_i)
#         final_k = self.conv_r(x_k) + self.conv_i(x_j) - self.conv_j(x_i) + self.conv_k(x_r)

#         # Stack results: shape [B, qc2, 4, H', W']
#         out_stacked = torch.stack([final_r, final_i, final_j, final_k], dim=2)

#         # Apply Batch Norm (IQBN handles the internal reshape) and Activation
#         # bn receives [B, qc2, 4, H', W'] and operates across the full c2 channels.
#         return self.act(self.bn(out_stacked))
    

