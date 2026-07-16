# Catalyst UI

Vite/React sidecar for the governed Catalyst question → preview → accepted
execution → typed table workflow. The UI always calls the same-origin
`/v1/catalyst` API; the production Nginx image proxies that path to
`catalyst-gateway:8000`, so browser CORS configuration is not required.

## Local commands

```bash
npm ci
npm run dev
npm test
npm run lint
npm run typecheck
npm run build
```

## Browser tests

The `deterministic` Playwright project intercepts the Catalyst API. The
`demo-video` project also uses deterministic responses by default and always
records video, traces, and screenshots.

```bash
npx playwright install chromium
npm run test:e2e -- --project=deterministic
npm run test:e2e -- --project=demo-video
```

Set `PLAYWRIGHT_BASE_URL` to run against a deployed UI, `PLAYWRIGHT_QUERY` to
override the golden question, and `PLAYWRIGHT_USE_MOCK_API=false` to exercise
the deployed same-origin Catalyst API in the `demo-video` project.
