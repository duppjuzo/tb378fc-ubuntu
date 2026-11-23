#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-2.0-only
# Build the RAM-only TB378FC GPT partitioning & recovery setup initramfs (.cpio.gz).
set -euo pipefail

repo=$(cd -- "$(dirname -- "$0")/.." && pwd)
busybox_bin=""
out_file=""
cc_bin="${CROSS_COMPILE:-aarch64-linux-gnu-}gcc"
gen_init_cpio=""

usage() {
	cat <<'EOF'
Usage: build-setup-initramfs.sh --busybox PATH --output PATH [--cc CC] [--gen-init-cpio PATH]
EOF
}

while [[ $# -gt 0 ]]; do
	case "$1" in
	--busybox) busybox_bin=$2; shift 2 ;;
	--output) out_file=$2; shift 2 ;;
	--cc) cc_bin=$2; shift 2 ;;
	--gen-init-cpio) gen_init_cpio=$2; shift 2 ;;
	-h|--help) usage; exit 0 ;;
	*) echo "Unknown argument: $1" >&2; usage >&2; exit 2 ;;
	esac
done

[[ -n $busybox_bin && -n $out_file ]] || { usage >&2; exit 2; }
[[ -f $busybox_bin ]] || { echo "Missing busybox binary: $busybox_bin" >&2; exit 1; }

stage=$(mktemp -d)
trap 'rm -rf "$stage"' EXIT

"$cc_bin" -O2 -static -Wall -Wextra -Werror \
	-o "$stage/tb378fc-gpt-split" "$repo/tools/gpt-split/tb378fc-gpt-split.c"
"$cc_bin" -O2 -static -Wall -Wextra -Werror \
	-o "$stage/reboot-bootloader" "$repo/tools/gpt-split/reboot-bootloader.c"

cat >"$stage/desktop-init" <<'EOS'
#!/bin/sh
# SPDX-License-Identifier: GPL-2.0-only
export PATH=/bin:/sbin:/usr/bin:/usr/sbin
if [ -c /dev/watchdog0 ]; then
    watchdog -F -T 30 -t 5 /dev/watchdog0 &
fi
mkdir -p /run/evidence/cgi-bin

cmdline=$(cat /proc/cmdline)
autosplit_gib=""
for tok in $cmdline; do
    case "$tok" in
        tb378fc.autosplit=*) autosplit_gib=${tok#tb378fc.autosplit=} ;;
    esac
done

# Wait up to 20s for UFS /dev/sda18 (stock userdata) or /dev/sda19 (existing ubuntu)
for _ in $(seq 1 20); do
    [ -b /dev/sda18 ] && break
    sleep 1
done

if [ -n "$autosplit_gib" ]; then
    echo "TB378FC_AUTOSPLIT_START gib=$autosplit_gib" | tee /run/evidence/autosplit.log >/dev/kmsg
    if /bin/tb378fc-gpt-split /dev/sda --ubuntu-gib "$autosplit_gib" --apply >>/run/evidence/autosplit.log 2>&1; then
        echo "TB378FC_AUTOSPLIT_OK" | tee -a /run/evidence/autosplit.log >/dev/kmsg
    else
        echo "TB378FC_AUTOSPLIT_FAILED" | tee -a /run/evidence/autosplit.log >/dev/kmsg
    fi
    sync
    sleep 2
    /bin/reboot-bootloader
    while :; do sleep 1; done
fi

cat >/run/evidence/cgi-bin/status <<'EOC'
#!/bin/sh
echo 'Content-Type: text/plain'
echo ''
echo '=== UNAME ==='
uname -a
echo '=== PARTITIONS ==='
grep -E 'sda([0-9]*)$' /proc/partitions
echo '=== GPT DRY RUN ==='
/bin/tb378fc-gpt-split /dev/sda 2>&1
EOC
chmod 0755 /run/evidence/cgi-bin/status

cat >/run/evidence/cgi-bin/apply-gpt <<'EOC'
#!/bin/sh
echo 'Content-Type: text/plain'
echo ''
gib=$(cat | tr -dc '0-9')
[ -n "$gib" ] || gib=120
/bin/tb378fc-gpt-split /dev/sda --ubuntu-gib "$gib" --apply 2>&1
echo "EXIT_CODE=$?"
EOC
chmod 0755 /run/evidence/cgi-bin/apply-gpt

cat >/run/evidence/cgi-bin/reboot-fastboot <<'EOC'
#!/bin/sh
echo 'Content-Type: text/plain'
echo ''
echo 'REBOOTING_TO_FASTBOOT'
( sleep 1; /bin/reboot-bootloader ) >/dev/null 2>&1 &
EOC
chmod 0755 /run/evidence/cgi-bin/reboot-fastboot

while :; do
    dmesg >/run/evidence/dmesg.txt 2>&1 || true
    sleep 5
done
EOS
chmod 0755 "$stage/desktop-init"
printf 'tb378fc-v7.4-setup
' >"$stage/build-id"

applets=(
	sh ash cat chmod chown chroot cp cut date dd df dmesg echo env expr find findfs
	grep head httpd id ifconfig ip kill ln losetup ls mkdir mknod mount mv
	pidof poweroff printf ps pwd readlink reboot rm rmdir sed seq setsid
	sleep sort stat stty switch_root sync tail tee test touch tr true false
	udhcpd umount uname usleep watchdog wc xargs
)

out_file=$(realpath -m "$out_file")
mkdir -p "$(dirname "$out_file")"

if [[ -n $gen_init_cpio && -x $gen_init_cpio ]]; then
	{
		for d in /bin /dev /etc /lib /mnt /newroot /proc /run /sbin /sys /tmp /usr /usr/bin /usr/sbin; do
			printf 'dir %s 0755 0 0
' "$d"
		done
		printf 'nod /dev/console 0600 0 0 c 5 1
'
		printf 'nod /dev/null 0666 0 0 c 1 3
'
		printf 'file /init %s 0755 0 0
' "$repo/initramfs/init"
		printf 'file /bin/busybox %s 0755 0 0
' "$busybox_bin"
		printf 'file /bin/desktop-init %s 0755 0 0
' "$stage/desktop-init"
		printf 'file /bin/tb378fc-gpt-split %s 0755 0 0
' "$stage/tb378fc-gpt-split"
		printf 'file /bin/reboot-bootloader %s 0755 0 0
' "$stage/reboot-bootloader"
		printf 'file /bin/tb378fc-shutdown %s 0755 0 0
' "$repo/initramfs/shutdown"
		printf 'file /bin/usb-diag %s 0755 0 0
' "$repo/initramfs/usb-diag"
		printf 'file /etc/build-id %s 0644 0 0
' "$stage/build-id"
		for app in "${applets[@]}"; do
			printf 'slink /bin/%s busybox 0777 0 0
' "$app"
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
	install -m 0755 "$stage/desktop-init" "$root/bin/desktop-init"
	install -m 0755 "$stage/tb378fc-gpt-split" "$root/bin/tb378fc-gpt-split"
	install -m 0755 "$stage/reboot-bootloader" "$root/bin/reboot-bootloader"
	install -m 0755 "$repo/initramfs/shutdown" "$root/bin/tb378fc-shutdown"
	install -m 0755 "$repo/initramfs/usb-diag" "$root/bin/usb-diag"
	cp "$stage/build-id" "$root/etc/build-id"
	find "$root" -exec touch -h -d '@0' {} +
	(
		cd "$root"
		find . -mindepth 1 -printf '%P
' | LC_ALL=C sort | 			cpio -o -H newc --owner=0:0 --quiet | gzip -9n >"$out_file"
	)
fi

echo "Built setup initramfs: $out_file ($(stat -c '%s' "$out_file") bytes)"
