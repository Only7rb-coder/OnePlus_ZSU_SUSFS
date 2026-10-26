# OnePlus ZSU + SUSFS

OnePlus/Oppo/Realme device-specific kernel build automation based on [huangdihd/OnePlus_BakaSU_SUSFS](https://github.com/huangdihd/OnePlus_BakaSU_SUSFS) and WildKernels. The build integrates the actual [ZSU kernel](https://github.com/Only7rb-coder/zsu), not a BakaSU kernel with a renamed APK.

**Initial release published:** [v2.3.0-zsu-r1](https://github.com/Only7rb-coder/OnePlus_ZSU_SUSFS/releases/tag/v2.3.0-zsu-r1), built for **OnePlus 12 / OOS16 / kernel 6.1.118**. [Full source-build and release run](https://github.com/Only7rb-coder/OnePlus_ZSU_SUSFS/actions/runs/39176753694) succeeded. Only this initial target has been full-image compile-validated; retaining 158 upstream configurations is not a tested-device claim. The release is experimental: no phone has been flashed or hardware boot-tested by this task.

## ZSU-only manager policy

The kernel is pinned to ZSU commit `1044ca19e7a9ef5bed1e4e28ed8f56ac368616a4`, paired with the regular `v1.4.10` manager. Manager authentication uses the ZSU APK-v2 signing certificate SHA-256 `ba1159a5819dec96cdcb2498bef73170b0ba1735c12139bde1b7677bf7c5c2ab`, certificate DER size `0x318`, and package `com.zsu.zsu`. Debug mode and manager bypass are disabled.

Use the **regular** `ZSU_*-release.apk` from [ZSU releases](https://github.com/Only7rb-coder/zsu/releases/tag/v1.4.10). The spoofed/random-package APK is intentionally excluded. Generic KernelSU, KernelSU-Next and BakaSU managers are not accepted. This is manager authentication, not DRM: an app that is already root-authorized can still execute privileged operations.

No signing key is needed or included in this repository. The certificate fingerprint is public verification material. Build setup performs a sparse checkout of only ZSU `kernel/` and `uapi/`; it does not use the upstream setup script, which otherwise fetches generic KernelSU-Next.

## Features and compatibility

The upstream device configurations and pinned OnePlus manifests are retained. Their enabled features include SUSFS, BBG, HMBIRD SCX where configured, BBR/BBRv3, CAKE/PIE, LTO, optimization patches, TTL, IP sets/IPv6 NAT, TMPFS xattrs/POSIX ACL, Unicode bypass fixes, Droidspaces and NTSync. Support depends on the selected configuration and successful patch application/compilation.

SUSFS is pinned to v2.3.0 with a dedicated bridge for ZSU's modern kernel layout and control ABI. All five supported kernel families are pinned to upstream branch snapshots containing the priority UID filter. The ZSU bridge routes `CMD_SUSFS_SET_PRIORITY_UID_FILTER` and refreshes stored app UIDs when Android updates `packages.list`. It must not be enabled merely by setting defconfig flags: the ZSU-side Kconfig, lifecycle, permission and control handlers also need the integration patch. Builds fail closed if that patch is absent or critical configuration symbols disappear during `olddefconfig`.

The priority UID filter hides only selected package UIDs from `getpriority`, `setpriority`, `ioprio_get` and `ioprio_set` checks; it is not a general-purpose way to remove apps from every installed-app list. A compatible userspace control such as the updated `ksu_susfs set_priority_uid_filter <package>[,<package>...]` command or a manager UI must provide the package selection. This repository's workflow builds kernel ZIPs and does not package the SUSFS userspace module.

See [sources.lock.json](sources.lock.json) for source identities and [compatibility.md](compatibility.md) for the inherited compatibility notes. Confirm the exact model, ROM version and `adb shell uname -r` before selecting a kernel. A matching major/minor version alone is not sufficient.

## Build and publish

Use **Actions → Build and Release OnePlus ZSU Kernels → Run workflow**. Set `config_path` for one exact firmware/kernel target. An empty path builds the `selector` group. `selector=all` builds the full 158-configuration matrix.

```bash
gh workflow run build-kernel-release.yml \
  -R Only7rb-coder/OnePlus_ZSU_SUSFS \
  -f selector=all \
  -f make_release=false

gh run list -R Only7rb-coder/OnePlus_ZSU_SUSFS
gh run watch RUN_ID -R Only7rb-coder/OnePlus_ZSU_SUSFS --exit-status

# After all target jobs succeed, reuse that completed run to publish:
gh workflow run build-kernel-release.yml \
  -R Only7rb-coder/OnePlus_ZSU_SUSFS \
  -f retry_of_run=RUN_ID \
  -f make_release=true \
  -f release_tag=v2.3.0-zsu-all-r2 \
  -f prerelease=true
```

The release job runs only if every selected build succeeds. It checks ZIP CRCs, required AnyKernel3 files and the ARM64 Image header, then publishes fresh kernels with SHA256SUMS and source locks. It will not overwrite an existing tag or publish renamed upstream binaries. Keep the release a prerelease until a target device has been boot-tested.

Checksum-pinned SUSFS source archives, including their upstream license, are mirrored under `vendor/susfs-archives/` because GitLab returned HTTP 403 to the GitHub runner. `sources.lock.json` records every archive hash. The public upstream toolchain-cache release is reused to avoid duplicating multi-gigabyte toolchains. Missing mirror assets fail the build. No Telegram secrets or scheduled jobs are configured. Actions compilation is not evidence of boot compatibility.

## Source and licenses

Upstream build snapshot: `3f6f1193f4576799a09bf81c8923c7dbb3720272`. The original upstream README is preserved at [docs/UPSTREAM_README.md](docs/UPSTREAM_README.md). Credits remain with huangdihd, WildKernels, OnePlusOSS, KernelSU/KernelSU-Next, ZSU, simonpunk/SUSFS, AnyKernel3, BBG and the individual patch authors.

ZSU's `kernel/` is GPL-2.0-only; its other components are GPL-3.0-or-later. Kernel trees, SUSFS and external patches retain their own licenses. The upstream workflow repository has no repository-wide license file in this snapshot; this project does not invent or override one. Source revisions, OnePlus manifests and local integration patches are available here, and linked upstream projects provide their corresponding source and license notices. Preserve applicable notices when redistributing.

Flashing modified kernels can cause data loss or an unbootable device. Back up your data and original boot partitions, understand the recovery procedure, and never flash an artifact for a different model/firmware.
