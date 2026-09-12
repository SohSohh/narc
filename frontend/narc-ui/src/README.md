# NARC chat UI redesign

Drop these files into the existing React/Vite `src` directory:

- `App.jsx`
- `App.css`
- `index.css`
- `main.jsx` (unchanged, included for completeness)

The frontend keeps the existing backend contract:

- `POST /chat` with `{ message, session_id }`
- reads `session_id`, `answer`, `sources`, and `expires_in_seconds`
- `DELETE /chat/{session_id}` when starting a new conversation

No new npm package is required beyond the existing `react-markdown` and `remark-gfm` imports already used by the original frontend. Typography is loaded from Google Fonts; all icons are inline SVG.
