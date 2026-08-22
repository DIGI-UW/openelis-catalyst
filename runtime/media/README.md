# Demo media

Recordings the landing page and docs site link to, served by Caddy at
`/media/<name>` on the demo host.

**Nothing in this directory is committed.** Demo videos are binaries: a cut
re-recorded after every UI change is a new blob, git keeps every one forever,
and a repository that carries them gets slower for everyone who clones it. The
`.gitignore` beside this file enforces that.

Uploading a cut:

    scp -i ~/.ssh/aws-catalyst-demo/catalyst-demo-key.pem \
      openelis-lab-demo.mp4 \
      ubuntu@catalyst.openelis-global.org:~/catalyst-demo/targets/catalyst/runtime/media/

It is then live at `https://catalyst.openelis-global.org/media/openelis-lab-demo.mp4`.

**Give a new cut a new filename.** The route sets a one-week immutable
`Cache-Control`, so overwriting a name leaves stale copies in browser caches;
`openelis-lab-demo-2026-08.mp4` costs nothing and avoids that entirely.
