#!/usr/bin/env bash
# SPDX-License-Identifier: BSD-3-Clause
# Build a minimal, clean, rootless TB378FC initramfs (.cpio.gz).
set -euo pipefail

repo=$(cd -- "$(dirname -- "$0")/.." && pwd)
busybox_bin=""
out_file=""
root_uuid=""
build_id="tb378fc-v7.4"
gen_init_cpio=""

usage() {
	cat <<'EOF'
Usage: build-initramfs.sh --busybox PATH --output PATH [options]
  --busybox PATH        Path to static arm64 busybox binary
  --output PATH         Destination .cpio.gz path
  --gen-init-cpio PATH  Optional kernel usr/gen_init_cpio (creates /dev/console and /dev/null rootlessly)
  --root-uuid UUID      Optional fallback root filesystem UUID (written to /etc/root-uuid)
  --build-id ID         Optional build identifier (default: tb378fc-v7.4)
EOF
}

while [[ $# -gt 0 ]]; do
	case "$1" in
	--busybox) busybox_bin=$2; shift 2 ;;
	--output) out_file=$2; shift 2 ;;
	--gen-init-cpio) gen_init_cpio=$2; shift 2 ;;
	--root-uuid) root_uuid=$2; shift 2 ;;
	--build-id) build_id=$2; shift 2 ;;
	-h|--help) usage; exit 0 ;;
	*) echo "Unknown argument: $1" >&2; usage >&2; exit 2 ;;
	esac
done

[[ -n $busybox_bin && -n $out_file ]] || { usage >&2; exit 2; }
[[ -f $busybox_bin ]] || { echo "Missing busybox binary: $busybox_bin" >&2; exit 1; }

if command -v file >/dev/null 2>&1; then
	file_desc=$(file -b "$busybox_bin")
	[[ $file_desc == *"ARM aarch64"* ]] || {
		echo "Expected ARM64 busybox binary, got: $file_desc" >&2
		exit 1
	}
fi

stage=$(mktemp -d)
trap 'rm -rf "$stage"' EXIT

applets=(
	sh ash cat chmod chown chroot cp cut date dd df dmesg echo env expr find findfs
	grep head httpd id ifconfig ip kill ln losetup ls mkdir mknod mount mv
	pidof poweroff printf ps pwd readlink reboot rm rmdir sed seq setsid
	sleep sort stat stty switch_root sync tail tee test touch tr true false
	udhcpd umount uname usleep watchdog wc xargs
)

printf '%s\n' "$build_id" >"$stage/build-id"
if [[ -n $root_uuid ]]; then
	printf '%s\n' "$root_uuid" >"$stage/root-uuid"
fi

out_file=$(realpath -m "$out_file")
mkdir -p "$(dirname "$out_file")"

if [[ -n $gen_init_cpio ]]; then
	[[ -x $gen_init_cpio ]] || { echo "gen_init_cpio is not executable: $gen_init_cpio" >&2; exit 1; }
	{
		for d in /bin /dev /etc /lib /mnt /newroot /proc /run /sbin /sys /tmp /usr /usr/bin /usr/sbin; do
			printf 'dir %s 0755 0 0\n' "$d"
		done
		printf 'nod /dev/console 0600 0 0 c 5 1\n'
		printf 'nod /dev/null 0666 0 0 c 1 3\n'
		printf 'file /init %s 0755 0 0\n' "$repo/initramfs/init"
		printf 'file /bin/busybox %s 0755 0 0\n' "$busybox_bin"
		printf 'file /bin/tb378fc-shutdown %s 0755 0 0\n' "$repo/initramfs/shutdown"
		printf 'file /bin/usb-diag %s 0755 0 0\n' "$repo/initramfs/usb-diag"
		printf 'file /etc/build-id %s 0644 0 0\n' "$stage/build-id"
		if [[ -f $stage/root-uuid ]]; then
			printf 'file /etc/root-uuid %s 0644 0 0\n' "$stage/root-uuid"
		fi
		for app in "${applets[@]}"; do
			printf 'slink /bin/%s busybox 0777 0 0\n' "$app"
		done
	} >"$stage/cpio.list"
	"$gen_init_cpio" -t 0 "$stage/cpio.list" | gzip -9n >"$out_file"
else
	root="$stage/root"
	mkdir -p "$root"/{bin,dev,etc,lib,mnt,newroot,proc,run,sbin,sys,tmp,usr/bin,usr/sbin}
	install -m 0755 "$busybox_bin" "$root/bin/busybox"
	for app in "${applets[@]}"; do
		ln -s busybox "$root/bin/$app"
	done
	install -m 0755 "$repo/initramfs/init" "$root/init"
	install -m 0755 "$repo/initramfs/shutdown" "$root/bin/tb378fc-shutdown"
	install -m 0755 "$repo/initramfs/usb-diag" "$root/bin/usb-diag"
	cp "$stage/build-id" "$root/etc/build-id"
	if [[ -f $stage/root-uuid ]]; then
		cp "$stage/root-uuid" "$root/etc/root-uuid"
	fi
	find "$root" -exec touch -h -d '@0' {} +
	(
		cd "$root"
		find . -mindepth 1 -printf '%P\n' | LC_ALL=C sort | \
			cpio -o -H newc --owner=0:0 --quiet | gzip -9n >"$out_file"
	)
fi

echo "Built initramfs: $out_file ($(stat -c '%s' "$out_file") bytes)"
