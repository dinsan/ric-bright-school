# Lumina Web

The frontend for Lumina, a learning platform for Grades 1 to 12. The backend has moved to [`dinsan/ric-bright-school-backend`](https://github.com/dinsan/ric-bright-school-backend); this repository contains the Next.js web application.

| Layer | Stack |
| --- | --- |
| Frontend | Next.js 16 (App Router), React 19, TypeScript (strict), Tailwind CSS 4, TanStack Query |
| Tooling | npm, ESLint, TypeScript |

## Getting started

### Prerequisites

- Node.js and npm
- The Lumina API running locally, unless you are working on UI that does not need API data

Follow the backend repository's [README](https://github.com/dinsan/ric-bright-school-backend#readme) to start the API. Its local default address is `http://localhost:8000`.

### Run the web app

From this repository root:

```bash
cd frontend
cp .env.example .env.local   # optional; defaults point to localhost:8000
npm install
npm run dev
```

Open http://localhost:3000. The API docs are at http://localhost:8000/docs when the backend is running.

To run checks:

```bash
cd frontend
npm run lint
npm run typecheck
npm run build
```

## Configuration

Frontend environment variables are documented in [`frontend/.env.example`](frontend/.env.example):

- `NEXT_PUBLIC_API_URL` is used by browser requests and is inlined into the client bundle at build time.
- `API_URL` is used for server-side rendering. It falls back to `NEXT_PUBLIC_API_URL` if unset.

Set these to the reachable API base URL when it is not `http://localhost:8000`. For Docker or other deployments, use an address reachable from the respective browser or server runtime.

## Project structure

```text
frontend/src/
├── app/       routes, layout, pages, error and not-found screens
├── features/  feature-specific API calls, hooks, types and components
├── components/shared UI and layout
├── lib/       API client, environment and helpers
└── hooks/ · config/
```

The frontend talks to the separately maintained [Lumina API](https://github.com/dinsan/ric-bright-school-backend). Backend setup, API documentation, migrations and tests are maintained in that repository.
