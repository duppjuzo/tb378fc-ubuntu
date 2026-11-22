// SPDX-License-Identifier: GPL-2.0-only
// Hardware Adreno 830 GPU WebRender + DMABUF zero-copy + Qualcomm Iris VA-API (H.264/HEVC/VP9/AV1) for Lenovo TB378FC.
pref("gfx.webrender.all", true);
// Keep experimental Wayland native subsurface compositor disabled on Linux:
// wrCompositor triggers zwp_linux_buffer_params_v1 plane modifier protocol errors
// and Firefox 160 ps_quad_textured TEXTURE_RECT panics on Bilibili HEVC/DMABUF.
pref("gfx.webrender.compositor", false);
pref("gfx.webrender.compositor.force-enabled", false);
pref("gfx.canvas.accelerated", true);
pref("widget.dmabuf.enabled", true);
pref("widget.dmabuf.force-enabled", true);
pref("widget.wayland.opaque-region.enabled", true);
pref("widget.wayland.vsync.keep-firing-at-idle", false);
pref("media.hardware-video-decoding.enabled", true);
pref("media.hardware-video-decoding.force-enabled", true);
pref("media.hardware-video-decoding.failed", false);
pref("media.ffmpeg.vaapi.enabled", true);
pref("media.hevc.enabled", true);
pref("media.hardware-video-decoding-vulkan.enabled", false);
pref("media.ffvpx.enabled", true);
pref("media.rdd-ffmpeg.enabled", true);
pref("media.av1.enabled", true);
pref("layout.frame_rate", 0);

// Lenovo TB378FC Display P3 wide-gamut color management
pref("gfx.color_management.mode", 1);
pref("gfx.color_management.enablev4", true);
pref("gfx.color_management.rendering_intent", 0);
