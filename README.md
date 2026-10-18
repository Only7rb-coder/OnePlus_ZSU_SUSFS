# OnePlus ZSU + SUSFS

OnePlus/Oppo/Realme device-specific kernel build automation based on [huangdihd/OnePlus_BakaSU_SUSFS](https://github.com/huangdihd/OnePlus_BakaSU_SUSFS) and WildKernels. The build integrates the actual [ZSU kernel](https://github.com/Only7rb-coder/zsu), not a BakaSU kernel with a renamed APK.

**Status: integration under validation. A retained configuration is not a tested-device claim.** Initial releases must remain experimental until the exact kernel boots successfully on its target firmware. No phone has been flashed or boot-tested by this task.

## ZSU-only manager policy

The kernel is pinned to ZSU commit `1044ca19e7a9ef5bed1e4e28ed8f56ac368616a4`, corresponding to the `v1.4.10` release source. Manager authentication uses the ZSU APK-v2 signing certificate SHA-256 `ba1159a5819dec96cdcb2498bef73170b0ba1735c12139bde1b7677bf7c5c2ab`, certificate DER size `0x318`, and package `com.zsu.zsu`. Debug mode and manager bypass are disabled.

Use the **regular** `ZSU_*-release.apk` from [ZSU releases](https://github.com/Only7rb-coder/zsu/releases/tag/v1.4.10). The spoofed/random-package APK is intentionally excluded. Generic KernelSU, KernelSU-Next and BakaSU managers are not accepted. This is manager authentication, not DRM: an app that is already root-authorized can still execute privileged operations.

No signing key is needed or included in this repository. The certificate fingerprint is public verification material. Build setup performs a sparse checkout of only ZSU `kernel/` and `uapi/`; it does not use the upstream setup script, which otherwise fetches generic KernelSU-Next.

## Features and compatibility

The upstream device configurations and pinned OnePlus manifests are retained. Their enabled features include SUSFS, BBG, HMBIRD SCX where configured, BBR/BBRv3, CAKE/PIE, LTO, optimization patches, TTL, IP sets/IPv6 NAT, TMPFS xattrs/POSIX ACL, Unicode bypass fixes, Droidspaces and NTSync. Support depends on the selected configuration and successful patch application/compilation.

SUSFS is pinned to v2.3.0 with a dedicated bridge for ZSU's modern kernel layout and control ABI. It must not be enabled merely by setting defconfig flags: the ZSU-side Kconfig, lifecycle, permission and control handlers also need the integration patch. Builds fail closed if that patch is absent or critical configuration symbols disappear during `olddefconfig`.

See [sources.lock.json](sources.lock.json) for source identities and [compatibility.md](compatibility.md) for the inherited compatibility notes. Confirm the exact model, ROM version and `adb shell uname -r` before selecting a kernel. A matching major/minor version alone is not sufficient.

## Build and publish

Use **Actions → Build and Release OnePlus ZSU Kernels → Run workflow**. Set `config_path` for one exact firmware/kernel target. An empty path builds the `selector` group. Start with one target; broad groups can contain many independent builds.

```bash
gh workflow run build-kernel-release.yml \
  -R Only7rb-coder/OnePlus_ZSU_SUSFS \
  -f config_path=configs/oos16/OP12-6.1.118.json \
  -f make_release=true \
  -f release_tag=v2.3.0-zsu-r1 \
  -f prerelease=true

gh run list -R Only7rb-coder/OnePlus_ZSU_SUSFS
gh run watch RUN_ID -R Only7rb-coder/OnePlus_ZSU_SUSFS --exit-status
```

The command's OP12 configuration is a **pipeline example**, not a recommendation for your phone. The release job runs only if every selected build succeeds. It checks ZIP CRCs, required AnyKernel3 files and the ARM64 Image header, then publishes fresh kernels with SHA256SUMS and source locks. It will not overwrite an existing tag or publish renamed upstream binaries.

Checksum-pinned SUSFS source archives, including their upstream license, are mirrored under `vendor/susfs-archives/` because GitLab returned HTTP 403 to the GitHub runner. `sources.lock.json` records every archive hash. The public upstream toolchain-cache release is reused to avoid duplicating multi-gigabyte toolchains. Missing mirror assets fail the build. No Telegram secrets or scheduled jobs are configured. Actions compilation is not evidence of boot compatibility.

## Source and licenses

Upstream build snapshot: `3f6f1193f4576799a09bf81c8923c7dbb3720272`. The original upstream README is preserved at [docs/UPSTREAM_README.md](docs/UPSTREAM_README.md). Credits remain with huangdihd, WildKernels, OnePlusOSS, KernelSU/KernelSU-Next, ZSU, simonpunk/SUSFS, AnyKernel3, BBG and the individual patch authors.

ZSU's `kernel/` is GPL-2.0-only; its other components are GPL-3.0-or-later. Kernel trees, SUSFS and external patches retain their own licenses. The upstream workflow repository has no repository-wide license file in this snapshot; this project does not invent or override one. Source revisions, OnePlus manifests and local integration patches are available here, and linked upstream projects provide their corresponding source and license notices. Preserve applicable notices when redistributing.

Flashing modified kernels can cause data loss or an unbootable device. Back up your data and original boot partitions, understand the recovery procedure, and never flash an artifact for a different model/firmware.
