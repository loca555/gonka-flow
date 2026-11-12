# Gonka Flow

- Do not ask for a seed/private key and do not add signing/sending without a separate request.
- Do not replace RPC errors with zero balances or volumes.
- Amounts are integer strings; do not use float for financial aggregates.
- Do not treat a bridge/transfer/LP as a trade or an address as an identity.
- Do not claim full coverage by min/max: keep the real intervals.
- Do not advance cursors of a failed batch.
- Publishing to Render is allowed by the user only on the free plan; do not create paid resources.
- GitHub — loca555 only. Do not publish .env, .tools, working databases, keys or research from the parent folder.
- Do not touch research files in the parent folder.
- Before handing off: unittest, an API check and a visual/interactive UI test.

## Known failure of the built-in Windows browser

- On this computer the Node REPL of the built-in browser crashes regularly: `windows sandbox failed: helper_unknown_error: apply deny-read ACLs` / `node_repl kernel exited unexpectedly` (confirmed on 7 September 2026; keep this context for future tasks).
- Follow the mandatory browser skill procedure, but after one attempt with the same error do not repeat the launch and diagnostics in a loop. For the local UI use the permitted fallback: a separate headless Chrome via Playwright with a clean profile, without real wallets or user data.
- Do not weaken the sandbox/ACL and do not bypass the restrictions on controlling the built-in tab. Do not present a separate test browser as a check of the user extension.

## Database snapshot before a Render update

- Before every deploy to Render, update the saved public database snapshot to a fresh verified block so that after a restart the site immediately shows current history.
- Before publishing, verify the snapshot integrity, the completeness of the saved history and restoration from the archive. Include the updated archive and its manifest in the release.
- Use the sanitized public snapshot; do not publish the working database, secrets or private research. When restoring, do not overwrite a fresher existing database.

## Local development before publishing

- Make and verify all new changes locally first, then show the user the local result.
- Publish new changes to Render only after a separate explicit request from the user. A previous deploy does not mean permission to publish subsequent changes automatically.

## Operating mode since 4 October 2026
- Local development and running only. Do not publish to Render anymore.
- Update the main Docker container gonka-flow at http://127.0.0.1:8017, keeping the gonka-flow-data volume.

- Development and launch checks go through Docker. The main gonka-flow container is on 8017; do not leave extra uvicorn servers running outside Docker.
