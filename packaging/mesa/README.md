# Mesa 26.2.6 Patches for Lenovo TB378FC (Adreno 830v1 / SM8750P)

This directory syncs the generic SM8750 / Adreno 830v1 Mesa 26.2.6 patch stack
from upstream [`onepluspad-2pro-mainline`](https://github.com/yzddmr6/onepluspad-2pro-mainline)
(`v0.12.1`, commit `fad774fa045256d63cf6b11ae9be7a1dcb58efb5`). All patches are
purely GPU/driver fixes (Freedreno, Turnip, IR3, Mesa core) and contain zero
board-specific code.

## Key Fixes Included

- **`freedreno-thread-safe-tex-seqno.patch`**: Fixes the root cause of multi-threaded
  Freedreno `fd6_texture_state` crashes (`util_idalloc_mt` for sampler/view seqno
  allocation across frontend and `threaded_context` driver threads). Because this
  patch fixes the race at its root, `GALLIUM_THREAD=false` has been removed from
  `org.gnome.Shell@.service.d`.
  - *Fallback note*: If you temporarily run an older unpatched Mesa build on
    device, you may re-enable `Environment=GALLIUM_THREAD=false` in a local user
    systemd drop-in (`~/.config/systemd/user/org.gnome.Shell@.service.d/override.conf`)
    until upgrading Mesa.
- **`freedreno-a830v1.patch`**, **`freedreno-explicit-sync.patch`**, **`freedreno-syncobj-fd.patch`**:
  Adreno 830v1 enablement and explicit sync / syncobj FD handling.
- **`piano-fixes.patch`**: Generic Mesa core fix publishing shared GL contexts only
  after `st_context` initialization completes.
- **`ir3-float-helper-order.patch`**, **`ir3-float-saturation.patch`**, **`ir3-relative-const-alias.patch`**:
  IR3 shader compiler correctness fixes.
- **`turnip-a8xx-runtime-unroll.patch`**, **`turnip-a8xx-word-reuse.patch`**, **`turnip-ssbo-wide-reads.patch`**:
  Turnip Vulkan a8xx fixes.

## Cross-Compilation Compiler Requirement

> **Use GCC 14 (`gcc-14-aarch64-linux-gnu` / `g++-14-aarch64-linux-gnu`) to cross-compile Mesa.**
> Upstream documented that GCC 13 under `arm64 -O3` miscompiles Mesa GLSL shaders.
