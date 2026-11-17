# Android installation and validation

> For the Phase 4 preview APK and emulator evidence, see [Phase 4 status and validation](PHASE4_STATUS_AND_VALIDATION.md).

Download `app-debug-apk` from the successful [Phase 3 Android workflow run](https://github.com/arafatislambksmd378-lgtm/phone-ai-agent-team/actions/runs/44016902968), built from commit `5b2da63`; do not use the older Phase 2 PR artifact. GitHub reported the artifact exists, but this sandbox could not download it from external artifact storage, so its contents and installability were not independently inspected. For later app changes, use the matching successful latest-commit run. A debug APK is not a production-signed release. Install using `adb install -r app-debug.apk`, or transfer it to the device and allow installation from that source. Build locally with JDK 17, Android SDK 34, Gradle 8.9: `cd android && gradle testDebugUnitTest assembleDebug`.

Open Settings and enter the HTTPS backend URL and its API bearer token. On Android emulator `http://10.0.2.2:8000` is allowed for local development; on-device loopback refers to that device, not the server. No GitHub token belongs in the app. Create/select a project, connect a dedicated GitHub test repo, select a non-default working branch, submit instructions, browse/edit files, compare branches, review a PR and authorize a merge in Approvals. Delete and merge require confirmation; a stale-file conflict must preserve edits until the user chooses how to resolve them.

No physical device or emulator was available in this environment. All of the above device operations still require validation; a passing CI build alone is not a device test. Existing app settings use Android Keystore and are not backed up. Debug builds are not suitable for production distribution.
