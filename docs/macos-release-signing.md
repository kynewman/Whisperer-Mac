# macOS Release Signing

Whisperer must be signed with a Developer ID Application certificate for public
macOS releases. macOS stores Accessibility/TCC approvals against the app's
designated requirement; ad-hoc signatures are tied to a specific build and make
users re-approve auto-paste after updates.

## Required Local Setup

1. Import a Developer ID Application certificate into the login keychain.
2. Store notarization credentials:

   ```zsh
   xcrun notarytool store-credentials whisperer-notary \
     --apple-id you@example.com \
     --team-id TEAMID \
     --password app-specific-password
   ```

3. Build, sign, notarize, staple, and verify:

   ```zsh
   WHISPERER_CODESIGN_IDENTITY="Developer ID Application: Your Name (TEAMID)" \
   WHISPERER_NOTARY_KEYCHAIN_PROFILE=whisperer-notary \
   ./build_macos.sh
   ```

For non-ad-hoc builds, notarization is required by default. Use
`WHISPERER_SKIP_NOTARIZATION=1` only for private signed test builds that will
not be distributed.

The release verifier fails if the app inside the DMG is ad-hoc signed,
cdhash-only, missing a Team ID, or missing an Apple-anchored designated
requirement.

## Local Development Builds

For private local testing only:

```zsh
WHISPERER_ALLOW_ADHOC_SIGNING=1 ./build_macos.sh
```

Never publish an ad-hoc build. It will not preserve Accessibility permission
reliably across updates on other Macs.
