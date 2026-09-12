# NARC frontend

A responsive NUST assistant built with React and Vite.

## Development

Run `npm ci`, then `npm run dev`. Copy `.env.example` to `.env.local` to override `VITE_API_URL`. This public build-time value must be an HTTP(S) backend base URL; never place secrets in VITE variables. The hosted backend is the default.

## Production

Run `npm run lint`, `node --test tests/chat.test.mjs`, and `npm run build`. Deploy `dist/` to a static HTTPS host. `npm run preview` is for local build inspection. Configure the backend to allow the deployed origin through CORS. Set your host?s security headers and cache hashed assets immutably; revalidate index.html on each visit.

The API contract is POST /chat with { message, session_id }, returning { answer, session_id, sources }. New conversation clears local state, cancels pending requests and attempts DELETE /chat/{session_id}. Requests time out after 60 seconds and failed questions can be retried. Conversations remain in memory only.

Animations use CSS, finish automatically except for the pending indicator, and respect reduced motion. Markdown loads on demand. Typography uses system fonts with no third-party font requests.

Before release, verify live backend connectivity and CORS from the deployed origin. Test keyboard navigation, mobile keyboards, source links, retries and new conversation during a pending request.
