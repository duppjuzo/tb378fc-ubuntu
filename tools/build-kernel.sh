#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-2.0-only
# Reproducible ARM64 cross-build for Lenovo Xiaoxin Pad Pro GT 13 (TB378FC).
set -euo pipefail

repo=$(cd -- "$(dirname -- "$0")/.." && pwd)
src=${KERNEL_SOURCE:-"$repo/cache/linux-sm8750-erhai"}
out=${OUT_DIR:-"$repo/out/kernel-tb378fc"}
jobs=${JOBS:-$(nproc)}
cross=${CROSS_COMPILE:-aarch64-linux-gnu-}
with_waydroid=${WITH_WAYDROID:-1}
use_ccache=${USE_CCACHE:-1}

if [[ $# != 0 ]]; then
	echo "Usage: $0 (KERNEL_SOURCE, OUT_DIR, JOBS, CROSS_COMPILE, WITH_WAYDROID=1|0 optional)" >&2
	exit 2
fi

src=$(realpath "$src")
[[ $(git -C "$src" rev-parse --show-toplevel) == "$src" ]] || {
	echo "Kernel source must be a Git checkout on a case-sensitive ext4 filesystem: $src" >&2
	exit 1
}

# Guard against building on Windows NTFS mounts (/mnt/c/... etc.)
fstype=$(stat -f -c '%T' "$src" 2>/dev/null || true)
case "$fstype" in
	v9fs|drvfs|ntfs|fuseblk|cifs|smb2)
		echo "Refusing to build Linux kernel on case-insensitive/NTFS mount ($fstype): $src" >&2
		exit 1
		;;
esac

pinned_base=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["kernel"]["commit"])' "$repo/kernel/source.json")
head=$(git -C "$src" rev-parse HEAD)
git -C "$src" merge-base --is-ancestor "$pinned_base" "$head" || {
	echo "Kernel source HEAD ($head) does not descend from pinned base $pinned_base." >&2
	exit 1
}
[[ -z $(git -C "$src" status --porcelain --untracked-files=no) ]] || {
	echo "Commit or stash tracked kernel changes before building." >&2
	exit 1
}

fragments=(
	"$src/arch/arm64/configs/sm8750-netfilter.config"
	"$src/arch/arm64/configs/sm8750-peripherals.config"
	"$src/arch/arm64/configs/sm8750-snap.config"
	"$src/arch/arm64/configs/sm8750-virt.config"
	"$src/arch/arm64/configs/sm8750-desktop.config"
)
if [[ $with_waydroid == 1 ]]; then
	fragments+=("$src/arch/arm64/configs/sm8750-waydroid.config")
fi
fragments+=("$repo/kernel/configs/tb378fc.config")

for fragment in "${fragments[@]}"; do
	[[ -f $fragment ]] || { echo "Missing config fragment: $fragment" >&2; exit 1; }
done

mkdir -p "$out"
out=$(realpath "$out")
exec 9>"$out/.build.lock"
flock -n 9 || { echo "Build output is already in use: $out" >&2; exit 1; }

export ARCH=arm64
export CROSS_COMPILE=$cross
export KBUILD_BUILD_USER=tb378fc
export KBUILD_BUILD_HOST=mainline
export KCFLAGS=${KCFLAGS:-""}
export KAFLAGS=${KAFLAGS:-""}

args=(-C "$src" "O=$out" "ARCH=arm64" "CROSS_COMPILE=$cross")
if [[ $use_ccache == 1 ]] && command -v ccache >/dev/null 2>&1; then
	args+=("CC=ccache ${cross}gcc")
fi

rust_identity='{"enabled":false}'
if [[ $with_waydroid == 1 ]]; then
	rust_identity=$(python3 "$repo/tools/kernel-rust-identity.py")
	args+=(
		"RUSTC=$RUSTC"
		"BINDGEN=$BINDGEN"
		"RUST_LIB_SRC=$RUST_LIB_SRC"
		"LIBCLANG_PATH=$LIBCLANG_PATH"
	)
fi
printf '%s
' "$rust_identity" >"$out/rust-toolchain.json"

toolchain=$("${cross}gcc" --version | head -1)
recipe=$(cat "${fragments[@]}" "$src/arch/arm64/configs/defconfig" | sha256sum | cut -d' ' -f1)
identity=$(printf '%s
' "$src" "$pinned_base" "$toolchain" "$cross" "$with_waydroid" "$KCFLAGS" "$KAFLAGS" "$rust_identity" | sha256sum | cut -d' ' -f1)

if [[ -f $out/.build-input && $(cat "$out/.build-input") != "$identity" ]]; then
	echo "Toolchain/source settings changed; cleaning stale config in $out" >&2
	rm -f "$out/.config" "$out/.recipe"
fi
printf '%s
' "$identity" >"$out/.build-input"

if [[ ! -f $out/.config || ! -f $out/.recipe || $(cat "$out/.recipe") != "$recipe" ]]; then
	echo "Configuring kernel ($out)..."
	make "${args[@]}" defconfig >"$out/configure.log" 2>&1
	"$src/scripts/kconfig/merge_config.sh" -m -O "$out" "$out/.config" "${fragments[@]}" >>"$out/configure.log" 2>&1
	make "${args[@]}" olddefconfig >>"$out/configure.log" 2>&1
	printf '%s
' "$recipe" >"$out/.recipe"
fi

# Verify effective merged Kconfig expectations (later fragments override earlier ones).
python3 - "$out/.config" "${fragments[@]}" <<'PY'
import pathlib, re, sys

cfg_lines = set(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8").splitlines())
expected = {}
for frag_path in sys.argv[2:]:
    for raw in pathlib.Path(frag_path).read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        m_set = re.match(r"^(CONFIG_[A-Za-z0-9_]+)=(.+)$", line)
        m_off = re.match(r"^# (CONFIG_[A-Za-z0-9_]+) is not set$", line)
        if m_set:
            expected[m_set.group(1)] = ("set", line, frag_path)
        elif m_off:
            expected[m_off.group(1)] = ("off", line, frag_path)

errors = []
for sym, (kind, line, frag_path) in expected.items():
    if kind == "set":
        if line not in cfg_lines:
            errors.append(f"Requested kernel setting missing: {line} ({frag_path})")
    else:
        if f"{sym}=y" in cfg_lines or f"{sym}=m" in cfg_lines:
            errors.append(f"Forbidden kernel setting enabled: {sym} ({frag_path})")

if errors:
    for err in errors:
        print(err, file=sys.stderr)
    raise SystemExit(1)
print(f"Verified {len(expected)} effective Kconfig assertions cleanly.")
PY

compiler_sha=$(sha256sum "$(command -v "${cross}gcc")" | cut -d' ' -f1)
config_sha=$(sha256sum "$out/.config" | cut -d' ' -f1)
abi=$(printf '%s
' "$head" "$config_sha" "$toolchain" "$compiler_sha" "$rust_identity" | sha256sum | cut -d' ' -f1)
localversion="-g${head:0:12}-a${abi:0:8}"
args+=("LOCALVERSION=$localversion")

echo "Building TB378FC kernel $head ($localversion) with $toolchain ($jobs jobs, waydroid=$with_waydroid)..."
make "${args[@]}" -j"$jobs" Image qcom/sm8750-lenovo-tb378fc.dtb modules 2>&1 | tee "$out/build.log"

v4l2_src=${V4L2LOOPBACK_SRC:-}
if [[ -z $v4l2_src ]]; then
	for cand in "$repo/cache/v4l2loopback" "/root/tb378fc-work/out/v4l2loopback-build"; do
		if [[ -f "$cand/v4l2loopback.c" ]]; then
			v4l2_src=$cand
			break
		fi
	done
fi
rm -rf "$out/extra-modules"
if [[ -n $v4l2_src && -f "$v4l2_src/v4l2loopback.c" ]]; then
	echo "Building matched out-of-tree v4l2loopback.ko from $v4l2_src..."
	make "${args[@]}" M="$v4l2_src" clean >/dev/null 2>&1 || true
	make "${args[@]}" M="$v4l2_src" -j"$jobs" modules 2>&1 | tee -a "$out/build.log"
	krel_built=$(cat "$out/include/config/kernel.release")
	vmagic=$(modinfo -F vermagic "$v4l2_src/v4l2loopback.ko" 2>/dev/null || true)
	[[ $vmagic == "$krel_built "* ]] || {
		echo "v4l2loopback.ko vermagic mismatch: got '$vmagic', expected prefix '$krel_built '" >&2
		exit 1
	}
	mkdir -p "$out/extra-modules"
	cp "$v4l2_src/v4l2loopback.ko" "$out/extra-modules/v4l2loopback.ko"
fi

[[ $(git -C "$src" rev-parse HEAD) == "$head" && -z $(git -C "$src" status --porcelain --untracked-files=no) ]] || {
	echo "Kernel source changed during build; output must not be used." >&2
	exit 1
}

python3 - "$out" "$head" "$pinned_base" "$toolchain" "$compiler_sha" "$localversion" "$with_waydroid" <<'PY'
import hashlib, json, pathlib, sys
out = pathlib.Path(sys.argv[1])
head, base, compiler, compiler_sha, localversion, with_waydroid = sys.argv[2:]
files = [
    "arch/arm64/boot/Image",
    "arch/arm64/boot/dts/qcom/sm8750-lenovo-tb378fc.dtb",
    ".config",
]
sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
modules = {}
for name in (out / "modules.order").read_text(encoding="utf-8").splitlines():
    if not name.strip():
        continue
    path = pathlib.Path(name.strip())
    if path.suffix == ".o":
        path = path.with_suffix(".ko")
    assert not path.is_absolute() and ".." not in path.parts, name
    modules[str(path)] = sha(out / path)
assert modules, "configured module build produced no modules"
result = {
    "status": "OFFLINE_BUILD_ONLY",
    "device": "lenovo-tb378fc",
    "upstream_base_commit": base,
    "kernel_commit": head,
    "compiler": compiler,
    "compiler_sha256": compiler_sha,
    "localversion": localversion,
    "kernel_release": (out / "include/config/kernel.release").read_text(encoding="utf-8").strip(),
    "with_waydroid": with_waydroid == "1",
    "rust_toolchain": json.loads((out / "rust-toolchain.json").read_text(encoding="utf-8")),
    "files": {f: sha(out / f) for f in files},
    "module_count": len(modules),
    "modules": modules,
    "extra_modules": {
        f"updates/{p.name}": sha(p)
        for p in sorted((out / "extra-modules").glob("*.ko"))
    } if (out / "extra-modules").is_dir() else {},
    "module_metadata": {
        name: sha(out / name)
        for name in (
            "modules.order",
            "modules.builtin",
            "modules.builtin.modinfo",
            "Module.symvers",
            "System.map",
        )
        if (out / name).is_file()
    },
}
(out / "build-result.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
PY

echo "TB378FC kernel build complete: $out/build-result.json ($(cat "$out/include/config/kernel.release"))"
