"""
Skills system — interactive Q&A → fully tailored agent prompt per tech stack.
newsite and newapp ask for frontend + backend tech separately and produce
framework-specific instructions with a frontend/ and backend/ folder structure.
Custom skills from ~/.bharatcode/skills/*.md are also supported as-is.
"""
from pathlib import Path
from rich.console import Console

SKILLS_DIR = Path.home() / ".bharatcode" / "skills"
console    = Console()

# ── Tech stack option labels ───────────────────────────────────────────────────

FE_OPTIONS = [
    "React + Vite",
    "Vue 3 + Vite",
    "Next.js 14",
    "Angular 17",
    "Svelte + Vite",
    "Vanilla HTML / CSS / JS",
]

BE_OPTIONS = [
    "Flask (Python)",
    "Django + DRF (Python)",
    "Node.js + Express",
    "FastAPI (Python)",
    "Go + Gin",
    "None  (static / frontend only)",
]

BE_OPTIONS_APP = [   # newapp — backend is required
    "Flask (Python)",
    "Django + DRF (Python)",
    "Node.js + Express",
    "FastAPI (Python)",
    "Go + Gin",
]

# ── Per-skill questions ────────────────────────────────────────────────────────

SKILL_QUESTIONS: dict[str, list[dict]] = {
    "newsite": [
        {"key": "name",     "label": "Site name",                                          "required": True},
        {"key": "type",     "label": "What kind of site",
         "choices": ["portfolio", "landing page", "SaaS marketing", "e-commerce",
                     "blog", "agency", "product showcase", "community", "other"],
         "required": True},
        {"key": "desc",     "label": "Describe it — what it does, who it's for",
         "hint":  "e.g. dark developer portfolio with projects, blog, and contact form"},
        {"key": "frontend", "label": "Frontend technology",
         "choices": FE_OPTIONS,                                                             "required": True},
        {"key": "backend",  "label": "Backend  (choose None for static / no API needed)",
         "choices": BE_OPTIONS,                                                             "required": True},
        {"key": "sections", "label": "Key sections",
         "hint":  "e.g. hero, about, projects, skills, pricing, blog, testimonials, contact"},
        {"key": "features", "label": "Special features",
         "hint":  "e.g. dark mode toggle, contact form with backend, blog CMS, animations"},
        {"key": "theme",    "label": "Visual theme",
         "choices": ["dark", "light", "minimal", "bold / colorful", "corporate", "playful"]},
    ],

    "newapp": [
        {"key": "name",     "label": "App name",                                           "required": True},
        {"key": "desc",     "label": "What does this app do?",                             "required": True},
        {"key": "frontend", "label": "Frontend technology",
         "choices": FE_OPTIONS,                                                             "required": True},
        {"key": "backend",  "label": "Backend technology",
         "choices": BE_OPTIONS_APP,                                                         "required": True},
        {"key": "database", "label": "Database",
         "choices": ["PostgreSQL", "MySQL", "MongoDB", "SQLite", "Redis + PostgreSQL", "none"]},
        {"key": "auth",     "label": "Authentication",
         "choices": ["JWT  (email / password)", "JWT + Google OAuth", "Session-based", "no auth"]},
        {"key": "features", "label": "Core features  (comma-separated)",
         "hint":  "e.g. user profiles, dashboard, file uploads, real-time notifications, admin panel"},
        {"key": "extras",   "label": "Extra integrations  (optional)",
         "hint":  "e.g. Stripe, SendGrid, WebSockets, S3, Redis cache, cron jobs"},
    ],

    "docker": [
        {"key": "database",   "label": "Database to include in compose",
         "choices": ["PostgreSQL", "MySQL", "MongoDB", "Redis", "none"]},
        {"key": "extras",     "label": "Extra services",
         "choices": ["Redis + Celery", "Nginx reverse proxy", "both", "none"]},
        {"key": "multistage", "label": "Multi-stage build  (smaller production image)?",
         "choices": ["yes", "no"]},
    ],

    "ci-github": [
        {"key": "test_fw",    "label": "Test framework",
         "hint":  "e.g. pytest, Jest, JUnit, go test — or 'none'"},
        {"key": "deploy",     "label": "Deployment target",
         "choices": ["AWS EC2", "AWS ECS / ECR", "GCP Cloud Run", "Azure App Service",
                     "VPS (SSH deploy)", "Heroku", "none"]},
        {"key": "auto_deploy","label": "Auto-deploy on merge to main?",
         "choices": ["yes", "no"]},
    ],
}


# ── Interactive Q&A ───────────────────────────────────────────────────────────

def _ask_choice(label: str, choices: list[str]) -> str | None:
    try:
        import questionary
        from questionary import Style
        result = questionary.select(
            f"  {label}:",
            choices=choices + ["↩  Skip"],
            style=Style([
                ("highlighted", "fg:cyan bold"),
                ("pointer",     "fg:cyan bold"),
                ("selected",    "fg:green"),
                ("question",    "fg:yellow bold"),
            ]),
            instruction=" (↑↓ move  Enter select)",
        ).ask()
        return None if result == "↩  Skip" else result
    except (ImportError, Exception):
        console.print(f"\n  [yellow]{label}:[/yellow]")
        for i, c in enumerate(choices, 1):
            console.print(f"    [green]{i}[/green]  {c}")
        try:
            raw = input("  Pick number (Enter to skip): ").strip()
        except (EOFError, KeyboardInterrupt):
            return None
        if raw.isdigit():
            idx = int(raw) - 1
            if 0 <= idx < len(choices):
                return choices[idx]
        return None


def ask_skill_questions(name: str, prefilled: dict | None = None) -> dict | None:
    """
    Run the interactive Q&A for a skill.
    prefilled: keys already known — those questions are skipped.
    Returns answers dict, or None if the user cancelled.
    """
    questions = SKILL_QUESTIONS.get(name)
    if not questions:
        return {}

    pre     = prefilled or {}
    answers = dict(pre)

    console.print(f"\n[bold cyan]  {name} setup[/bold cyan]\n")

    for q in questions:
        key      = q["key"]
        label    = q["label"]
        hint     = q.get("hint", "")
        choices  = q.get("choices")
        required = q.get("required", False)

        if key in pre and pre[key]:
            console.print(f"  [dim]{label}:[/dim] [cyan]{pre[key]}[/cyan]")
            continue

        while True:
            if choices:
                result = _ask_choice(label, choices)
                if result is None and required:
                    console.print("  [red]Required — please pick one.[/red]")
                    continue
                if result:
                    answers[key] = result
                break
            else:
                if hint:
                    console.print(f"  [dim]{hint}[/dim]")
                try:
                    val = input(f"  {label}: ").strip()
                except (EOFError, KeyboardInterrupt):
                    console.print("\n  [dim]Cancelled.[/dim]")
                    return None
                if not val and required:
                    console.print("  [red]Required.[/red]")
                    continue
                if val:
                    answers[key] = val
                break

    console.print()
    return answers


# ── Per-framework detail blocks ───────────────────────────────────────────────

_FE_PORTS = {
    "React + Vite":            5173,
    "Vue 3 + Vite":            5173,
    "Svelte + Vite":           5173,
    "Next.js 14":              3000,
    "Angular 17":              4200,
    "Vanilla HTML / CSS / JS": 3000,
}

_BE_PORTS = {
    "Flask (Python)":        5000,
    "Django + DRF (Python)": 8000,
    "Node.js + Express":     5000,
    "FastAPI (Python)":      8000,
    "Go + Gin":              8080,
    "None  (static / frontend only)": None,
}


def _fe_detail(tech: str) -> str:
    """Detailed file structure + coding rules for the chosen frontend tech."""

    if tech == "React + Vite":
        return """
FRONTEND: React + Vite
══════════════════════
Folder structure (frontend/):
  src/
    components/
      ui/           ← Button.jsx, Input.jsx, Modal.jsx, Card.jsx, Spinner.jsx
                       (pure presentational, no API calls, fully typed props)
      layout/       ← Navbar.jsx, Footer.jsx, Sidebar.jsx, PageWrapper.jsx
      [feature]/    ← feature-specific components  (one sub-folder per domain)
    pages/          ← one file per route: HomePage.jsx, DashboardPage.jsx, LoginPage.jsx
    hooks/          ← useAuth.js, useFetch.js, useForm.js, useDebounce.js
    services/
      api.js        ← axios instance + request/response interceptors  (see below)
      auth.service.js    ← login(creds), register(data), logout(), refreshToken()
      [domain].service.js ← one file per API domain
    store/          ← Zustand store (preferred) or Context + useReducer
      authStore.js  ← user, token, setUser, clearAuth
    utils/
      formatters.js ← formatDate(), formatCurrency(), truncate()
      validators.js ← isEmail(), isStrongPassword(), required()
      constants.js  ← ROUTES, USER_ROLES, API_PATHS
    config/
      api.js        ← export const API_BASE = import.meta.env.VITE_API_URL
    styles/
      index.css     ← global reset, CSS custom properties, font-face
  App.jsx           ← <BrowserRouter> + <Routes> setup ONLY
  main.jsx          ← ReactDOM.createRoot + StrictMode

services/api.js (complete — copy this pattern exactly):
  import axios from 'axios';
  const api = axios.create({
    baseURL: import.meta.env.VITE_API_URL,
    headers: { 'Content-Type': 'application/json' },
  });
  api.interceptors.request.use(config => {
    const token = localStorage.getItem('token');
    if (token) config.headers.Authorization = `Bearer ${token}`;
    return config;
  });
  api.interceptors.response.use(
    response => response,
    async error => {
      if (error.response?.status === 401) {
        localStorage.removeItem('token');
        window.location.href = '/login';
      }
      return Promise.reject(error);
    }
  );
  export default api;

Domain service pattern (services/auth.service.js):
  import api from './api.js';
  export const authService = {
    login:    (data)  => api.post('/api/auth/login', data).then(r => r.data),
    register: (data)  => api.post('/api/auth/register', data).then(r => r.data),
    logout:   ()      => api.post('/api/auth/logout').then(r => r.data),
    me:       ()      => api.get('/api/auth/me').then(r => r.data),
  };

vite.config.js (dev proxy — eliminates CORS in development):
  import { defineConfig } from 'vite';
  import react from '@vitejs/plugin-react';
  export default defineConfig({
    plugins: [react()],
    server: {
      port: 5173,
      proxy: { '/api': { target: 'http://localhost:BACKEND_PORT', changeOrigin: true } }
    }
  });

.env.example:
  VITE_API_URL=http://localhost:BACKEND_PORT

package.json essentials:
  react@18, react-dom@18, react-router-dom@6, axios, zustand
  devDeps: vite, @vitejs/plugin-react

Coding rules:
  - ALL API calls go through services/*.service.js — never axios/fetch directly in components
  - All pages in pages/, reusable UI in components/ui/, layout in components/layout/
  - React Router v6: useNavigate(), useParams(), <Outlet /> pattern
  - No class components — functional components + hooks only
  - Zustand for global state (auth, user, theme) — useState/useReducer for local state
  - Never hardcode backend URLs in component files
  - Error boundaries at the page level
  - PropTypes or TypeScript interface for every component's props"""

    if tech == "Vue 3 + Vite":
        return """
FRONTEND: Vue 3 + Vite
══════════════════════
Folder structure (frontend/):
  src/
    components/
      base/         ← BaseButton.vue, BaseInput.vue, BaseModal.vue, BaseCard.vue
      layout/       ← AppHeader.vue, AppFooter.vue, AppSidebar.vue
      [feature]/    ← feature-specific components
    views/          ← one .vue file per route: HomeView.vue, DashboardView.vue, LoginView.vue
    router/
      index.js      ← createRouter, createWebHistory, route guards
    stores/         ← Pinia (one store per domain)
      auth.store.js ← useAuthStore: state, login(), logout(), fetchMe()
      [domain].store.js
    services/
      api.js        ← axios instance + interceptors
      [domain].service.js
    composables/    ← useAuth.js, useForm.js, usePagination.js, useNotify.js
    utils/
      formatters.js
      validators.js
    config/
      api.js        ← export const API_BASE = import.meta.env.VITE_API_URL
    assets/
      styles/
        main.css
        variables.css
  App.vue
  main.js           ← createApp + use(router) + use(pinia) + mount

services/api.js (same axios pattern as React — copy):
  import axios from 'axios';
  const api = axios.create({ baseURL: import.meta.env.VITE_API_URL });
  api.interceptors.request.use(config => {
    const token = localStorage.getItem('token');
    if (token) config.headers.Authorization = `Bearer ${token}`;
    return config;
  });
  api.interceptors.response.use(r => r, err => {
    if (err.response?.status === 401) { localStorage.removeItem('token'); window.location.href = '/login'; }
    return Promise.reject(err);
  });
  export default api;

Pinia store pattern (stores/auth.store.js):
  import { defineStore } from 'pinia';
  import { authService } from '../services/auth.service.js';
  export const useAuthStore = defineStore('auth', {
    state: () => ({ user: null, token: localStorage.getItem('token') || null }),
    getters: { isLoggedIn: s => !!s.token },
    actions: {
      async login(credentials) {
        const { token, user } = await authService.login(credentials);
        this.token = token; this.user = user; localStorage.setItem('token', token);
      },
      logout() { this.token = null; this.user = null; localStorage.removeItem('token'); },
    },
  });

vite.config.js:
  server: { port: 5173, proxy: { '/api': { target: 'http://localhost:BACKEND_PORT', changeOrigin: true } } }

.env.example: VITE_API_URL=http://localhost:BACKEND_PORT

Coding rules:
  - Composition API ONLY inside <script setup> — NEVER Options API
  - Pinia for all shared state — no Vuex, no prop drilling past 2 levels
  - All API calls in services/*.service.js — composables call services, components call composables
  - defineModel() macro (Vue 3.4+) for two-way binding in child components
  - Route guards in router/index.js using beforeEach for auth protection
  - Named routes always (router.push({ name: 'dashboard' }) not string paths)"""

    if tech == "Next.js 14":
        return """
FRONTEND: Next.js 14 (App Router)
══════════════════════════════════
Folder structure (frontend/):
  app/
    layout.tsx          ← root layout: <html>, <body>, global providers, fonts
    page.tsx            ← home route  (server component)
    globals.css
    (public)/           ← route group: public pages  (no auth needed)
      about/page.tsx
      [page]/page.tsx
    (auth)/             ← route group: login, signup
      login/page.tsx
      signup/page.tsx
    (dashboard)/        ← route group: protected pages
      layout.tsx        ← auth guard: redirect if no session
      page.tsx
      [feature]/page.tsx
    api/                ← API routes (ONLY for webhooks / third-party callbacks)
      health/route.ts
  components/
    ui/                 ← Button.tsx, Input.tsx, Card.tsx, Modal.tsx  ("use client")
    layout/             ← Header.tsx, Footer.tsx, Sidebar.tsx
    [feature]/
  lib/
    api.ts              ← typed fetch wrapper (see below)
    auth.ts             ← getSession(), requireAuth()
    db.ts               ← Prisma client (if DB in Next.js)
    utils.ts            ← cn(), formatDate(), formatCurrency()
  types/
    index.ts            ← all shared TypeScript interfaces/types
  hooks/                ← "use client" hooks: useAuth.ts, useForm.ts, useLocalStorage.ts
  config/
    site.ts             ← SITE_NAME, SITE_URL, nav links
  middleware.ts         ← protects /dashboard/** routes, redirects to /login
  next.config.js
  .env.example
  tsconfig.json

lib/api.ts (typed fetch wrapper):
  const BASE = process.env.NEXT_PUBLIC_API_URL ?? '';
  function getToken() { return typeof window !== 'undefined' ? localStorage.getItem('token') : null; }
  export async function apiFetch<T>(path: string, opts: RequestInit = {}): Promise<T> {
    const token = getToken();
    const res = await fetch(`${BASE}${path}`, {
      ...opts,
      headers: {
        'Content-Type': 'application/json',
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
        ...opts.headers,
      },
    });
    if (!res.ok) {
      const msg = await res.text();
      throw new Error(msg || `HTTP ${res.status}`);
    }
    return res.json() as Promise<T>;
  }
  export const api = {
    get:    <T>(path: string)              => apiFetch<T>(path, { method: 'GET' }),
    post:   <T>(path: string, body: unknown) => apiFetch<T>(path, { method: 'POST',   body: JSON.stringify(body) }),
    put:    <T>(path: string, body: unknown) => apiFetch<T>(path, { method: 'PUT',    body: JSON.stringify(body) }),
    delete: <T>(path: string)              => apiFetch<T>(path, { method: 'DELETE' }),
  };

next.config.js (API proxy to backend):
  /** @type {import('next').NextConfig} */
  module.exports = {
    async rewrites() {
      return [{ source: '/api/:path*', destination: `${process.env.BACKEND_URL}/api/:path*` }];
    },
  };

.env.example:
  NEXT_PUBLIC_API_URL=http://localhost:BACKEND_PORT
  BACKEND_URL=http://localhost:BACKEND_PORT

Coding rules:
  - Server components by default — add "use client" ONLY for: hooks, event listeners, browser APIs
  - Server Actions for all form mutations — not /api routes for internal data changes
  - /api routes ONLY for external webhooks (Stripe, GitHub, etc.)
  - TypeScript everywhere — no any, no @ts-ignore
  - NEXT_PUBLIC_ prefix for env vars needed in client components
  - next/image for ALL images, next/font for ALL fonts
  - Route groups (parentheses) to share layouts without adding URL segments
  - NEVER use Pages Router patterns (getServerSideProps, getStaticProps, pages/ directory)
  - middleware.ts: use next/server NextResponse.redirect for auth protection"""

    if tech == "Angular 17":
        return """
FRONTEND: Angular 17 (Standalone)
══════════════════════════════════
Folder structure (frontend/):
  src/app/
    core/
      guards/
        auth.guard.ts       ← CanActivateFn — redirects to /login if no token
      interceptors/
        auth.interceptor.ts ← HttpInterceptorFn — injects Bearer token into every request
        error.interceptor.ts ← handles 401 (redirect), 500 (show toast)
      services/
        auth.service.ts     ← login(), logout(), isLoggedIn(), currentUser signal
        api.service.ts      ← HttpClient wrapper (typed GET/POST/PUT/DELETE)
    shared/
      components/           ← ButtonComponent, InputComponent, ModalComponent, CardComponent
      pipes/                ← DateFormatPipe, CurrencyInrPipe
      directives/           ← ClickOutsideDirective, AutofocusDirective
    features/
      [feature]/
        components/
          [feature].component.ts   ← standalone, imports: CommonModule, RouterModule, ...
        services/
          [feature].service.ts     ← injects ApiService
        [feature].routes.ts        ← Routes array, lazy loaded
    app.component.ts        ← standalone root component
    app.config.ts           ← provideRouter, provideHttpClient, withInterceptors
    app.routes.ts           ← top-level routes with loadChildren lazy loading
  environments/
    environment.ts          ← { production: false, apiUrl: 'http://localhost:BACKEND_PORT' }
    environment.prod.ts     ← { production: true,  apiUrl: 'https://api.yourdomain.com' }
  proxy.conf.json           ← "/api": { "target": "http://localhost:BACKEND_PORT", "changeOrigin": true }

core/services/api.service.ts:
  @Injectable({ providedIn: 'root' })
  export class ApiService {
    private http = inject(HttpClient);
    private baseUrl = environment.apiUrl;
    get<T>(path: string)                  { return this.http.get<T>(`${this.baseUrl}${path}`); }
    post<T>(path: string, body: unknown)  { return this.http.post<T>(`${this.baseUrl}${path}`, body); }
    put<T>(path: string, body: unknown)   { return this.http.put<T>(`${this.baseUrl}${path}`, body); }
    delete<T>(path: string)               { return this.http.delete<T>(`${this.baseUrl}${path}`); }
  }

core/interceptors/auth.interceptor.ts:
  export const authInterceptor: HttpInterceptorFn = (req, next) => {
    const token = localStorage.getItem('token');
    if (token) {
      req = req.clone({ headers: req.headers.set('Authorization', `Bearer ${token}`) });
    }
    return next(req).pipe(
      catchError(err => { if (err.status === 401) { inject(Router).navigate(['/login']); } throw err; })
    );
  };

app.config.ts:
  export const appConfig: ApplicationConfig = {
    providers: [
      provideRouter(appRoutes),
      provideHttpClient(withInterceptors([authInterceptor, errorInterceptor])),
      provideAnimations(),
    ],
  };

angular.json (add proxyConfig):
  "serve": { "options": { "proxyConfig": "proxy.conf.json" } }

Coding rules:
  - Standalone components EVERYWHERE — never NgModules
  - Angular Signals: signal(), computed(), effect() for reactive state
  - inject() function in constructor body or at field declaration — never constructor injection style
  - All API calls in *.service.ts — components call services, never HttpClient directly
  - Lazy-load every feature route: loadChildren: () => import('./features/x/x.routes')
  - environment.ts for all config — never hardcode URLs
  - OnPush change detection strategy on all components"""

    if tech == "Svelte + Vite":
        return """
FRONTEND: Svelte + Vite
═══════════════════════
Folder structure (frontend/):
  src/
    components/
      ui/           ← Button.svelte, Input.svelte, Modal.svelte, Card.svelte
      layout/       ← Navbar.svelte, Footer.svelte
      [feature]/
    pages/          ← one .svelte per route (use svelte-routing or SvelteKit)
    stores/
      auth.js       ← writable store: { user, token }
      [domain].js
    services/
      api.js        ← fetch wrapper with auth header injection
      [domain].service.js
    utils/
      formatters.js
      validators.js
    config/
      api.js        ← export const API_BASE = import.meta.env.VITE_API_URL
    styles/
      global.css
      variables.css
  App.svelte
  main.js
  vite.config.js
  .env.example

stores/auth.js:
  import { writable } from 'svelte/store';
  function createAuthStore() {
    const { subscribe, set, update } = writable({
      user: null,
      token: localStorage.getItem('token') || null,
    });
    return {
      subscribe,
      login: (user, token) => {
        localStorage.setItem('token', token);
        set({ user, token });
      },
      logout: () => {
        localStorage.removeItem('token');
        set({ user: null, token: null });
      },
    };
  }
  export const authStore = createAuthStore();

services/api.js:
  import { get as getStore } from 'svelte/store';
  import { authStore } from '../stores/auth.js';
  const BASE = import.meta.env.VITE_API_URL;
  async function request(method, path, body) {
    const { token } = getStore(authStore);
    const res = await fetch(`${BASE}${path}`, {
      method,
      headers: {
        'Content-Type': 'application/json',
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      },
      ...(body ? { body: JSON.stringify(body) } : {}),
    });
    if (!res.ok) {
      const msg = await res.text();
      if (res.status === 401) { authStore.logout(); window.location.href = '/login'; }
      throw new Error(msg || `HTTP ${res.status}`);
    }
    return res.json();
  }
  export const api = {
    get:    (path)        => request('GET',    path),
    post:   (path, body)  => request('POST',   path, body),
    put:    (path, body)  => request('PUT',    path, body),
    delete: (path)        => request('DELETE', path),
  };

vite.config.js:
  server: { port: 5173, proxy: { '/api': { target: 'http://localhost:BACKEND_PORT', changeOrigin: true } } }

Coding rules:
  - Svelte stores for ALL shared state — components use $storeName reactive syntax
  - All API calls through services/api.js — never fetch() directly in .svelte files
  - $: reactive declarations for derived/computed values
  - onMount for lifecycle effects (like useEffect in React)
  - Dispatch custom events to parent instead of prop callbacks
  - Slots for component composition"""

    # Vanilla HTML / CSS / JS
    return """
FRONTEND: Vanilla HTML / CSS / JS
══════════════════════════════════
Folder structure (frontend/):
  index.html              ← main entry point (type="module" on script tags)
  [page].html             ← one file per page
  css/
    variables.css         ← ALL custom properties: colors, spacing, fonts, shadows, radii
    reset.css             ← modern CSS reset (box-sizing, margin 0, line-height)
    typography.css        ← @font-face / Google Fonts, heading scale, body text
    layout.css            ← .container, grid wrappers, section padding, flex rows
    navbar.css            ← nav links, hamburger, mobile overlay, scroll-shrink
    components.css        ← .btn, .card, .badge, .form-group, .input — every reusable piece
    [section].css         ← hero.css, about.css, projects.css, services.css, etc.
    animations.css        ← @keyframes, .reveal, .fade-in, hover transitions
    responsive.css        ← ALL media queries, every breakpoint
    main.css              ← @import in correct order (variables first, reset second)
  js/
    config.js             ← export const CONFIG = { API_BASE: 'http://localhost:BACKEND_PORT', ... }
    utils.js              ← $() querySelector, $$() querySelectorAll, debounce, throttle, formatDate
    api.js                ← fetch wrapper that reads CONFIG.API_BASE  (see below)
    auth.js               ← getToken(), setToken(), clearToken(), isLoggedIn()
    navbar.js             ← mobile toggle, scroll-hide/show, active link highlighting
    animations.js         ← IntersectionObserver scroll-reveal, counter animation, parallax
    theme.js              ← dark/light toggle, localStorage persistence
    forms.js              ← field validation, error display, submit handler
    [feature].js          ← one file per distinct feature
    main.js               ← DOMContentLoaded init — imports and calls init functions only
  assets/
    images/
    icons/
    fonts/

js/api.js (complete — copy exactly):
  import { CONFIG } from './config.js';
  import { getToken, clearToken } from './auth.js';
  async function request(method, path, body) {
    const token = getToken();
    const res = await fetch(`${CONFIG.API_BASE}${path}`, {
      method,
      headers: {
        'Content-Type': 'application/json',
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      },
      ...(body ? { body: JSON.stringify(body) } : {}),
    });
    if (res.status === 401) { clearToken(); window.location.href = '/login.html'; }
    if (!res.ok) { const msg = await res.text(); throw new Error(msg || `HTTP ${res.status}`); }
    if (res.status === 204) return null;
    return res.json();
  }
  export const api = {
    get:    (path)        => request('GET',    path),
    post:   (path, body)  => request('POST',   path, body),
    put:    (path, body)  => request('PUT',    path, body),
    delete: (path)        => request('DELETE', path),
  };

Coding rules:
  - ES6 modules (type="module") everywhere — no inline <script> blocks
  - ALL fetch calls go through api.js — never raw fetch() in feature files
  - CSS: use real values from the chosen palette — real pixel values, real colors, real spacing
  - HTML: real, meaningful content — never lorem ipsum in any visible text
  - JS: real event listeners, real DOM queries, real working logic
  - Every section gets its own .css file imported in main.css
  - config.js is the single source of truth for API URL and constants"""


def _be_detail(tech: str) -> str:
    """Detailed file structure + coding rules for the chosen backend tech."""

    if tech == "Flask (Python)":
        return """
BACKEND: Flask (Python)
═══════════════════════
Folder structure (backend/):
  app/
    __init__.py         ← create_app(config_name='development') factory function
    config.py           ← Config, DevelopmentConfig, ProductionConfig classes
    extensions.py       ← db = SQLAlchemy(); jwt = JWTManager(); cors = CORS()
    models/
      __init__.py
      user.py           ← class User(db.Model): id, email, password_hash, role, created_at, is_active
      [resource].py     ← one model file per domain entity
    routes/
      __init__.py       ← def register_routes(app): app.register_blueprint(auth_bp); ...
      auth.py           ← auth_bp = Blueprint('auth', __name__, url_prefix='/api/auth')
      [resource].py     ← [resource]_bp = Blueprint(...)
    services/
      auth_service.py   ← register_user(), authenticate_user(), create_tokens()
      [resource]_service.py
    utils/
      decorators.py     ← @admin_required, @validate_json(schema)
      validators.py     ← validate_email(), validate_password_strength()
      responses.py      ← success_response(data, code=200), error_response(msg, code=400)
  migrations/           ← flask db init, flask db migrate, flask db upgrade
  tests/
    test_auth.py
    test_[resource].py
  run.py                ← if __name__ == '__main__': create_app('development').run(port=5000, debug=True)
  requirements.txt
  .env.example
  Makefile

app/__init__.py (application factory — copy this pattern):
  from flask import Flask
  from .extensions import db, jwt, cors
  from .config import config
  def create_app(config_name='development'):
      app = Flask(__name__)
      app.config.from_object(config[config_name])
      db.init_app(app)
      jwt.init_app(app)
      cors.init_app(app, resources={r'/api/*': {'origins': app.config['CORS_ORIGINS']}})
      with app.app_context():
          db.create_all()
      from .routes import register_routes
      register_routes(app)
      return app

app/config.py:
  import os
  class Config:
      SECRET_KEY        = os.environ['SECRET_KEY']
      SQLALCHEMY_DATABASE_URI = os.environ['DATABASE_URL']
      JWT_SECRET_KEY    = os.environ['JWT_SECRET_KEY']
      JWT_ACCESS_TOKEN_EXPIRES  = timedelta(minutes=15)
      JWT_REFRESH_TOKEN_EXPIRES = timedelta(days=7)
      CORS_ORIGINS      = os.environ.get('CORS_ORIGINS', 'http://localhost:5173').split(',')
  class DevelopmentConfig(Config):
      DEBUG = True
      SQLALCHEMY_ECHO = True
  config = {'development': DevelopmentConfig, 'production': ProductionConfig}

Route pattern (every endpoint returns JSON):
  @auth_bp.route('/register', methods=['POST'])
  def register():
      data = request.get_json()
      if not data: return error_response('No data provided', 400)
      result, error = auth_service.register_user(data)
      if error: return error_response(error, 400)
      return success_response(result, 201)

  @auth_bp.route('/login', methods=['POST'])
  def login():
      data = request.get_json()
      tokens, error = auth_service.authenticate_user(data.get('email'), data.get('password'))
      if error: return error_response(error, 401)
      return success_response(tokens)

  @auth_bp.route('/me', methods=['GET'])
  @jwt_required()
  def me():
      user_id = get_jwt_identity()
      user = User.query.get_or_404(user_id)
      return success_response(user.to_dict())

Consistent response envelope (utils/responses.py):
  from flask import jsonify
  def success_response(data, code=200): return jsonify({'data': data, 'error': None}), code
  def error_response(msg, code=400):   return jsonify({'data': None, 'error': msg}),   code

GET /api/health endpoint (always include):
  @app.route('/api/health')
  def health(): return jsonify({'status': 'ok', 'timestamp': datetime.utcnow().isoformat()})

requirements.txt:
  flask>=3.0, flask-sqlalchemy, flask-jwt-extended, flask-cors,
  flask-migrate, psycopg2-binary, python-dotenv, bcrypt, email-validator

Coding rules:
  - NEVER use app = Flask(__name__) at module level — always application factory
  - One Blueprint per route group, all registered in routes/__init__.py
  - SQLAlchemy ORM only — never raw SQL strings
  - JWT via Flask-JWT-Extended: @jwt_required() on protected routes, get_jwt_identity()
  - All config from os.environ — never hardcode secrets
  - Passwords: bcrypt.generate_password_hash(pw, rounds=12) — never MD5/SHA
  - Every model has a .to_dict() method for JSON serialization"""

    if tech == "Django + DRF (Python)":
        return """
BACKEND: Django + REST Framework (Python)
══════════════════════════════════════════
Folder structure (backend/):
  config/
    __init__.py
    settings/
      __init__.py       ← from .development import *  (or set via DJANGO_SETTINGS_MODULE)
      base.py           ← installed apps, middleware, DRF config, JWT config
      development.py    ← DEBUG=True, local DB, CORS allow all
      production.py     ← DEBUG=False, production DB, ALLOWED_HOSTS, SECURE_* headers
    urls.py             ← urlpatterns: path('api/', include('apps.users.urls')), ...
    wsgi.py
    asgi.py
  apps/
    users/
      models.py         ← class User(AbstractUser): bio, avatar, ... (ALWAYS custom)
      serializers.py    ← UserSerializer, UserCreateSerializer, LoginSerializer
      views.py          ← UserViewSet, LoginView, RegisterView
      urls.py           ← router = DefaultRouter(); router.register('users', UserViewSet)
      permissions.py    ← IsOwnerOrReadOnly, IsAdmin
      tests.py
    [app]/              ← one Django app per domain (posts, products, orders, etc.)
  requirements.txt
  manage.py
  .env.example
  Makefile

config/settings/base.py essentials:
  AUTH_USER_MODEL = 'users.User'   ← MUST be set before first migration
  INSTALLED_APPS = [..., 'rest_framework', 'corsheaders', 'apps.users', ...]
  MIDDLEWARE = ['corsheaders.middleware.CorsMiddleware', ...]
  REST_FRAMEWORK = {
      'DEFAULT_AUTHENTICATION_CLASSES': ['rest_framework_simplejwt.authentication.JWTAuthentication'],
      'DEFAULT_PERMISSION_CLASSES':     ['rest_framework.permissions.IsAuthenticated'],
      'DEFAULT_PAGINATION_CLASS':       'rest_framework.pagination.PageNumberPagination',
      'PAGE_SIZE': 20,
  }
  SIMPLE_JWT = {
      'ACCESS_TOKEN_LIFETIME':  timedelta(minutes=15),
      'REFRESH_TOKEN_LIFETIME': timedelta(days=7),
  }
  CORS_ALLOWED_ORIGINS = env.list('CORS_ALLOWED_ORIGINS', default=['http://localhost:5173'])

ViewSet pattern (apps/[app]/views.py):
  class ArticleViewSet(ModelViewSet):
      serializer_class   = ArticleSerializer
      permission_classes = [IsAuthenticated]
      def get_queryset(self):
          return Article.objects.filter(author=self.request.user).select_related('author')
      def perform_create(self, serializer):
          serializer.save(author=self.request.user)

Serializer pattern:
  class ArticleSerializer(ModelSerializer):
      author = UserSerializer(read_only=True)
      class Meta:
          model  = Article
          fields = ['id', 'title', 'content', 'author', 'created_at']
          read_only_fields = ['id', 'author', 'created_at']

URL pattern (apps/[app]/urls.py):
  router = DefaultRouter()
  router.register(r'articles', ArticleViewSet, basename='article')
  urlpatterns = router.urls

Auth endpoints (add to config/urls.py):
  path('api/auth/token/',         TokenObtainPairView.as_view()),
  path('api/auth/token/refresh/', TokenRefreshView.as_view()),
  path('api/auth/register/',      RegisterView.as_view()),

GET /api/health/:
  path('api/health/', lambda req: JsonResponse({'status': 'ok'})),

requirements.txt:
  django>=5.0, djangorestframework, djangorestframework-simplejwt,
  django-cors-headers, django-environ, psycopg2-binary, pillow

Coding rules:
  - Custom User model from day 1 (AbstractUser) — impossible to change after first migration
  - DRF ViewSets + DefaultRouter for standard CRUD — class-based views always
  - JWT via simplejwt — never session auth for API endpoints
  - All settings from environment variables via django-environ
  - API versioned at /api/v1/ using namespace in urls.py
  - select_related / prefetch_related in every queryset — never N+1
  - Override get_queryset() to filter by request.user — never trust URL params for ownership"""

    if tech == "Node.js + Express":
        return """
BACKEND: Node.js + Express
══════════════════════════
Folder structure (backend/):
  src/
    config/
      db.js             ← DB connection (mongoose.connect or Prisma client init)
      env.js            ← zod or joi schema validating all required env vars on startup
    middleware/
      auth.middleware.js   ← verifyToken(req, res, next): checks Authorization header, attaches req.user
      error.middleware.js  ← (err, req, res, next): global error handler — LAST app.use()
      validate.middleware.js ← validate(schema)(req, res, next): validates req.body with zod/joi
      rateLimiter.js       ← express-rate-limit configuration
    routes/
      index.js          ← mount all routers: router.use('/auth', authRoutes); router.use('/...', ...)
      auth.routes.js    ← router.post('/register', validate(registerSchema), authController.register)
      [resource].routes.js ← router.use(authMiddleware) for protected routes
    controllers/
      auth.controller.js
        ← export const register = async (req, res, next) => { try { ... } catch (e) { next(e) } }
        ← export const login    = async (req, res, next) => { try { ... } catch (e) { next(e) } }
      [resource].controller.js
    models/
      User.js           ← Mongoose Schema / Prisma model definition
      [Resource].js
    services/
      auth.service.js   ← registerUser(data), loginUser(email, pw), refreshTokens(token)
      [resource].service.js ← ALL business logic lives here, not in controllers
    utils/
      jwt.js            ← signAccess(payload), signRefresh(payload), verifyToken(token)
      hash.js           ← hashPassword(pw), comparePassword(pw, hash)
      apiResponse.js    ← success(res, data, code=200), error(res, msg, code=400)
      AppError.js       ← class AppError extends Error { constructor(message, statusCode) }
  app.js                ← express setup, middleware, routes mounting
  server.js             ← app.listen(PORT) ONLY
  package.json
  .env.example

app.js (complete — copy this structure):
  import express from 'express';
  import cors from 'cors';
  import { routes } from './routes/index.js';
  import { errorMiddleware } from './middleware/error.middleware.js';
  const app = express();
  app.use(cors({ origin: process.env.CORS_ORIGIN, credentials: true }));
  app.use(express.json({ limit: '10mb' }));
  app.use(express.urlencoded({ extended: true }));
  app.get('/api/health', (_, res) => res.json({ status: 'ok', timestamp: new Date().toISOString() }));
  app.use('/api', routes);
  app.use(errorMiddleware);   // MUST be the last middleware
  export default app;

Controller pattern (copy exactly):
  export const register = async (req, res, next) => {
    try {
      const user = await authService.registerUser(req.body);
      return success(res, user, 201);
    } catch (err) { next(err); }
  };

Global error middleware (copy exactly):
  export const errorMiddleware = (err, req, res, next) => {
    const status  = err.statusCode || 500;
    const message = err.message    || 'Internal Server Error';
    res.status(status).json({ data: null, error: message });
  };

JWT pattern (utils/jwt.js):
  import jwt from 'jsonwebtoken';
  export const signAccess   = (payload) => jwt.sign(payload, process.env.ACCESS_TOKEN_SECRET,  { expiresIn: '15m' });
  export const signRefresh  = (payload) => jwt.sign(payload, process.env.REFRESH_TOKEN_SECRET, { expiresIn: '7d' });
  export const verifyToken  = (token, secret) => jwt.verify(token, secret);

package.json dependencies:
  express, jsonwebtoken, bcryptjs, cors, dotenv, mongoose OR @prisma/client,
  zod, express-rate-limit, morgan
  devDeps: nodemon, jest / vitest

Coding rules:
  - NEVER put business logic in controllers — controllers only call services and return responses
  - errorMiddleware is the LAST app.use() in app.js — nothing after it
  - async/await everywhere — zero callbacks
  - Validate request BEFORE controller via validate middleware
  - Refresh tokens stored in DB as SHA-256 hash — never the raw token
  - CORS_ORIGIN from env — never '*' in production
  - AppError class for all operational errors (wrong password, not found, etc.)
  - server.js only has: import app; app.listen(PORT, cb) — nothing else"""

    if tech == "FastAPI (Python)":
        return """
BACKEND: FastAPI (Python)
═════════════════════════
Folder structure (backend/):
  app/
    main.py             ← FastAPI() instance, lifespan, middleware, include_router
    config.py           ← class Settings(BaseSettings): reads from .env automatically
    database.py         ← engine, SessionLocal, Base, get_db Depends
    models/
      user.py           ← class User(Base): __tablename__, columns, relationships
      [resource].py
    schemas/
      user.py           ← UserCreate(BaseModel), UserUpdate, UserResponse, Token
      [resource].py     ← [Resource]Create, [Resource]Update, [Resource]Response
    routers/
      auth.py           ← router = APIRouter(prefix='/api/auth', tags=['auth'])
      [resource].py     ← router = APIRouter(prefix='/api/[resource]', tags=['[resource]'])
    dependencies/
      auth.py           ← get_current_user(token: str = Depends(oauth2_scheme), db = Depends(get_db))
    services/
      auth_service.py   ← async register_user(db, user_in), authenticate_user(db, email, pw)
      [resource]_service.py
    utils/
      security.py       ← hash_password(pw), verify_password(pw, hash), create_access_token(data)
  alembic/
    versions/
    env.py
  requirements.txt
  .env.example
  alembic.ini

app/main.py (complete):
  from fastapi import FastAPI
  from fastapi.middleware.cors import CORSMiddleware
  from contextlib import asynccontextmanager
  from .config import settings
  from .database import engine, Base
  from .routers import auth, [resource]
  @asynccontextmanager
  async def lifespan(app: FastAPI):
      Base.metadata.create_all(bind=engine)   # replace with alembic in prod
      yield
  app = FastAPI(title=settings.APP_NAME, version='1.0.0', lifespan=lifespan)
  app.add_middleware(CORSMiddleware,
      allow_origins=settings.CORS_ORIGINS, allow_credentials=True,
      allow_methods=['*'], allow_headers=['*'])
  app.include_router(auth.router)
  app.include_router([resource].router, dependencies=[Depends(get_current_user)])
  @app.get('/api/health')
  async def health(): return {'status': 'ok'}

app/config.py:
  from pydantic_settings import BaseSettings
  from typing import list
  class Settings(BaseSettings):
      APP_NAME:        str  = 'My App'
      DATABASE_URL:    str
      SECRET_KEY:      str
      ACCESS_TOKEN_EXPIRE_MINUTES: int = 15
      CORS_ORIGINS:    list[str] = ['http://localhost:5173']
      class Config: env_file = '.env'
  settings = Settings()

Router + schema pattern (copy exactly):
  @router.post('/register', response_model=UserResponse, status_code=201)
  async def register(user_in: UserCreate, db: Session = Depends(get_db)):
      existing = db.query(User).filter(User.email == user_in.email).first()
      if existing: raise HTTPException(status_code=400, detail='Email already registered')
      return await auth_service.register_user(db, user_in)

  @router.post('/login', response_model=Token)
  async def login(form: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
      user = await auth_service.authenticate_user(db, form.username, form.password)
      if not user: raise HTTPException(status_code=401, detail='Invalid credentials')
      token = create_access_token({'sub': str(user.id)})
      return {'access_token': token, 'token_type': 'bearer'}

dependencies/auth.py:
  oauth2_scheme = OAuth2PasswordBearer(tokenUrl='/api/auth/login')
  async def get_current_user(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)):
      try: payload = jwt.decode(token, settings.SECRET_KEY, algorithms=['HS256'])
      except JWTError: raise HTTPException(401, 'Could not validate credentials')
      user = db.query(User).filter(User.id == payload.get('sub')).first()
      if not user: raise HTTPException(401, 'User not found')
      return user

requirements.txt:
  fastapi>=0.110, uvicorn[standard], sqlalchemy>=2.0, alembic,
  pydantic-settings, python-jose[cryptography], passlib[bcrypt], psycopg2-binary, python-dotenv

Coding rules:
  - Type EVERYTHING — Pydantic models for all request/response bodies, never dict
  - Separate ORM models (models/) from Pydantic schemas (schemas/) — never reuse between layers
  - Depends() for every injected dependency: DB session, current user, settings
  - pydantic-settings reads .env automatically via env_file = '.env' in Config
  - HTTPException with status_code + detail for all error responses
  - Alembic for migrations in production — db.create_all() only in development
  - Auto-generated /docs (Swagger UI) — document it in README, it's a feature"""

    # Go + Gin
    return """
BACKEND: Go + Gin
═════════════════
Folder structure (backend/):
  cmd/server/
    main.go             ← entry point: load config, init DB, wire dependencies, start server
  internal/
    config/
      config.go         ← type Config struct; func Load() *Config — reads env via viper/godotenv
    db/
      db.go             ← func Connect(cfg *Config) *gorm.DB — opens DB, auto-migrate
    middleware/
      auth.go           ← JWT verification middleware: reads Authorization header, sets userID in context
      cors.go           ← gin-contrib/cors setup with allowed origins from config
      logger.go         ← request/response logging middleware
    handlers/
      auth.go           ← Register, Login, RefreshToken, Me — each returns gin.HandlerFunc
      [resource].go     ← List, Get, Create, Update, Delete
    models/
      user.go           ← type User struct { gorm.Model; Email string `gorm:"uniqueIndex"; ... }
      [resource].go
    repository/
      interfaces.go     ← type UserRepository interface { FindByEmail, Create, FindByID, ... }
      user_repo.go      ← type userRepo struct { db *gorm.DB }; implements UserRepository
      [resource]_repo.go
    services/
      auth_service.go   ← type AuthService interface; type authService struct { repo, cfg }
      [resource]_service.go
    router/
      router.go         ← func NewRouter(deps *Deps) *gin.Engine — mounts all routes
  pkg/
    jwt/
      jwt.go            ← GenerateAccessToken, GenerateRefreshToken, ValidateToken
    hash/
      hash.go           ← HashPassword, CheckPasswordHash (bcrypt)
    response/
      response.go       ← Success(c, data, code), Error(c, msg, code), Paginated(c, data, total)
  go.mod
  .env.example
  Makefile

cmd/server/main.go:
  func main() {
      cfg := config.Load()
      db  := db.Connect(cfg)
      userRepo    := repository.NewUserRepo(db)
      authService := services.NewAuthService(userRepo, cfg)
      r := router.NewRouter(&router.Deps{ AuthService: authService, Config: cfg })
      log.Printf("Server starting on :%s", cfg.Port)
      r.Run(":" + cfg.Port)
  }

router/router.go:
  func NewRouter(deps *Deps) *gin.Engine {
      r := gin.New()
      r.Use(gin.Recovery(), middleware.Logger(), middleware.CORS(deps.Config))
      api := r.Group("/api")
      api.GET("/health", func(c *gin.Context) { c.JSON(200, gin.H{"status": "ok"}) })
      auth := api.Group("/auth")
      {
          auth.POST("/register", handlers.Register(deps.AuthService))
          auth.POST("/login",    handlers.Login(deps.AuthService))
          auth.POST("/refresh",  handlers.RefreshToken(deps.AuthService))
      }
      protected := api.Group("/")
      protected.Use(middleware.AuthRequired(deps.Config.JWTSecret))
      {
          protected.GET("/auth/me", handlers.Me(deps.AuthService))
          [resource] := protected.Group("/[resource]")
          [resource].GET("",     handlers.List[Resource](deps.[Resource]Service))
          [resource].POST("",    handlers.Create[Resource](deps.[Resource]Service))
          [resource].GET("/:id", handlers.Get[Resource](deps.[Resource]Service))
          [resource].PUT("/:id", handlers.Update[Resource](deps.[Resource]Service))
      }
      return r
  }

Handler pattern (copy exactly):
  func Register(svc services.AuthService) gin.HandlerFunc {
      return func(c *gin.Context) {
          var req RegisterRequest
          if err := c.ShouldBindJSON(&req); err != nil {
              response.Error(c, err.Error(), http.StatusBadRequest); return
          }
          user, err := svc.Register(c.Request.Context(), req)
          if err != nil {
              response.Error(c, err.Error(), http.StatusBadRequest); return
          }
          response.Success(c, user, http.StatusCreated)
      }
  }

go.mod dependencies:
  github.com/gin-gonic/gin, gorm.io/gorm, gorm.io/driver/postgres,
  github.com/golang-jwt/jwt/v5, golang.org/x/crypto, github.com/spf13/viper,
  github.com/gin-contrib/cors

Coding rules:
  - Dependency injection via constructor functions (NewAuthService, NewUserRepo) — zero global state
  - Repository interface defined alongside service, implemented in repository/ — swap DB without changing service
  - Error wrapping: return fmt.Errorf("authService.Register: %w", err)
  - Handlers are closures that take a service — never directly access DB in handlers
  - All config from env vars via viper — never hardcode ports, secrets, DB strings
  - Table-driven tests for services and handlers"""


def _integration(fe: str, be: str) -> str:
    """Frontend ↔ Backend connectivity rules for any tech combo."""
    if be == "None  (static / frontend only)":
        return """
INTEGRATION: Static / Frontend Only
═════════════════════════════════════
No backend folder is needed for this project.
  - If a contact form is needed, use a third-party service: Formspree or EmailJS.
  - If data display is needed, use a public API or mock JSON files in assets/.
  - Document in README: "This is a static frontend — no server required."
  - Deploy to: Vercel, Netlify, or GitHub Pages."""

    fe_port = _FE_PORTS.get(fe, 5173)
    be_port = _BE_PORTS.get(be, 5000)

    # Proxy config per frontend
    if fe in ("React + Vite", "Vue 3 + Vite", "Svelte + Vite"):
        proxy_block = f"""vite.config.js dev proxy:
  server: {{
    port: {fe_port},
    proxy: {{ '/api': {{ target: 'http://localhost:{be_port}', changeOrigin: true }} }}
  }}"""
    elif fe == "Next.js 14":
        proxy_block = f"""next.config.js rewrites (proxy in development):
  async rewrites() {{
    return [{{ source: '/api/:path*', destination: 'http://localhost:{be_port}/api/:path*' }}];
  }}"""
    elif fe == "Angular 17":
        proxy_block = f"""proxy.conf.json:
  {{ "/api": {{ "target": "http://localhost:{be_port}", "secure": false, "changeOrigin": true }} }}
angular.json → architect.serve.options: {{ "proxyConfig": "proxy.conf.json" }}"""
    else:  # Vanilla
        proxy_block = f"""Vanilla JS: No dev proxy. Use the full backend URL directly.
  In js/config.js:  export const CONFIG = {{ API_BASE: 'http://localhost:{be_port}' }};
  In production, update CONFIG.API_BASE to your deployed backend URL."""

    # CORS config per backend
    if be == "Flask (Python)":
        cors_block = f"CORS(app, resources={{r'/api/*': {{origins: [\"http://localhost:{fe_port}\"]}}}})"
    elif be == "Django + DRF (Python)":
        cors_block = f"CORS_ALLOWED_ORIGINS = ['http://localhost:{fe_port}']"
    elif be == "Node.js + Express":
        cors_block = f"cors({{ origin: process.env.CORS_ORIGIN }})  # CORS_ORIGIN=http://localhost:{fe_port}"
    elif be == "FastAPI (Python)":
        cors_block = f"CORSMiddleware(allow_origins=['http://localhost:{fe_port}'])"
    else:  # Go + Gin
        cors_block = f"cors.Config{{AllowOrigins: []string{{\"http://localhost:{fe_port}\"}}}})"

    return f"""
INTEGRATION: {fe} ↔ {be}
{'═' * (len(fe) + len(be) + 4)}
Ports:
  Frontend: http://localhost:{fe_port}
  Backend:  http://localhost:{be_port}

Step 1 — Backend CORS (must allow the frontend origin):
  {cors_block}
  In production: set CORS origin from environment variable — never hardcode localhost.

Step 2 — Dev proxy (eliminates CORS errors during development):
  {proxy_block}

Step 3 — API health check (always implement this first):
  Backend:  GET /api/health → 200 {{"status": "ok", "timestamp": "..."}}
  Frontend: On app init, call GET /api/health to verify connectivity.

Step 4 — Consistent API response envelope (both sides must agree on this shape):
  Success: {{ "data": <payload>, "error": null }}
  Failure: {{ "data": null,     "error": "<human readable message>" }}
  HTTP status codes must match: 200 OK, 201 Created, 400 Bad Request, 401 Unauthorized, 404 Not Found, 500 Server Error.

Step 5 — Authentication flow (JWT):
  1. POST /api/auth/login → returns {{ access_token, refresh_token, user }}
  2. Frontend stores access_token in localStorage; stores refresh_token in httpOnly cookie or localStorage.
  3. Every subsequent request: Authorization: Bearer <access_token>
  4. On 401: try POST /api/auth/refresh → new access_token; on failure, redirect to login.
  5. Frontend api.js / api.service.js / ApiService handles this automatically in interceptors.

Step 6 — Environment variables (never hardcode URLs):
  frontend/.env:  VITE_API_URL=http://localhost:{be_port}   (or NEXT_PUBLIC_API_URL for Next.js)
  backend/.env:   PORT={be_port}, DATABASE_URL=..., SECRET_KEY=..., CORS_ORIGINS=http://localhost:{fe_port}
  Both: provide .env.example with every variable documented.

Step 7 — Folder structure at project root:
  project-root/
    frontend/   ← {fe} code
    backend/    ← {be} code
    README.md   ← complete setup instructions (both sides)

README.md must include:
  ## Prerequisites
  [list all required tools with version numbers]

  ## Setup & Run

  ### Backend
  cd backend
  [install command]   # pip install -r requirements.txt  OR  npm install  OR  go mod download
  [setup command]     # flask db upgrade  OR  python manage.py migrate  OR  npx prisma migrate dev
  [run command]       # flask run  OR  uvicorn app.main:app --reload  OR  npm run dev  OR  go run ./cmd/server

  ### Frontend
  cd frontend
  [install command]   # npm install
  [run command]       # npm run dev

  Both must run simultaneously. Open http://localhost:{fe_port} in your browser."""


# ── Prompt builders ───────────────────────────────────────────────────────────

def _build_newsite(answers: dict) -> str:
    name     = answers.get("name", "Website")
    site_type = answers.get("type", "website")
    desc     = answers.get("desc", "")
    fe       = answers.get("frontend", "React + Vite")
    be       = answers.get("backend",  "None  (static / frontend only)")
    sections = answers.get("sections", "")
    features = answers.get("features", "")
    theme    = answers.get("theme", "")

    req_lines = [f'Build a {site_type} called "{name}".']
    if desc:     req_lines.append(f"Description: {desc}")
    if theme:    req_lines.append(f"Visual theme: {theme}")
    if sections: req_lines.append(f"Sections: {sections}")
    if features: req_lines.append(f"Features: {features}")
    requirements = "\n".join(req_lines)

    be_str = be.replace("None  (static / frontend only)", "None")

    return f"""## PROJECT: {name}
## TYPE: {site_type}
{requirements}

## TECH STACK
Frontend: {fe}
Backend:  {be_str}

## STEP 1 — THINK BEFORE WRITING ANY CODE
Understand what this site is for, who uses it, and what impression it should make.
Design a color palette, typography, and layout that genuinely fit the project.
Plan every section, every page, every API endpoint before writing a single file.

## STEP 2 — FOLDER STRUCTURE (non-negotiable)
All code goes in TWO top-level folders:
  frontend/   ← all frontend code
  backend/    ← all backend code  (skip if backend is None)

{_fe_detail(fe)}

{_be_detail(be) if "None" not in be else ""}

{_integration(fe, be)}

## STEP 3 — DESIGN SYSTEM (use these — do NOT use grey defaults)

Pick one palette and use it consistently everywhere:

Option A — Dark (recommended for portfolios, SaaS, agency):
  :root {{
    --bg: #0f172a; --surface: #1e293b; --border: #334155;
    --primary: #6366f1; --primary-hover: #4f46e5; --accent: #06b6d4;
    --text: #f1f5f9; --text-muted: #94a3b8;
    --radius: 10px; --shadow: 0 4px 24px rgba(0,0,0,0.3);
  }}

Option B — Light (recommended for e-commerce, blog, corporate):
  :root {{
    --bg: #f8fafc; --surface: #ffffff; --border: #e2e8f0;
    --primary: #6366f1; --primary-hover: #4f46e5; --accent: #0ea5e9;
    --text: #0f172a; --text-muted: #64748b;
    --radius: 10px; --shadow: 0 2px 16px rgba(0,0,0,0.08);
  }}

Option C — Bold (recommended for landing pages, product showcase):
  :root {{
    --bg: #09090b; --surface: #18181b; --border: #27272a;
    --primary: #f97316; --primary-hover: #ea580c; --accent: #facc15;
    --text: #fafafa; --text-muted: #a1a1aa;
    --radius: 8px; --shadow: 0 4px 32px rgba(0,0,0,0.4);
  }}

Required component styles in every project:
  .btn-primary   {{ background: var(--primary); color: #fff; padding: 12px 24px; border-radius: var(--radius); font-weight: 600; border: none; cursor: pointer; }}
  .btn-primary:hover {{ background: var(--primary-hover); transform: translateY(-1px); }}
  .card          {{ background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius); padding: 28px; }}
  .section       {{ padding: 80px 0; }}
  .container     {{ max-width: 1200px; margin: 0 auto; padding: 0 24px; }}

Design rules (non-negotiable):
- HTML: REAL content — real headings, real body text, real copy. Zero lorem ipsum anywhere.
- CSS: real pixel/rem values from the palette above. Every element styled. Not a wireframe.
- JS: real working event listeners, real DOM manipulation, real logic.
- Mobile-first: works at 375px. Hamburger menu for mobile nav.
- Every section polished and complete — hero, cards, forms, footer.

## STEP 4 — WRITE EVERY FILE using <<<FILE:absolute/path>>> marker
Write every planned file completely. Do not skip any file.
Write CSS files before JS files. Write every section's CSS.
After all files, list what was created.
End with: what you built, design decisions, and exact commands to run it."""


def _build_newapp(answers: dict) -> str:
    name     = answers.get("name", "App")
    desc     = answers.get("desc", "")
    fe       = answers.get("frontend", "React + Vite")
    be       = answers.get("backend",  "Flask (Python)")
    database = answers.get("database", "PostgreSQL")
    auth     = answers.get("auth", "JWT  (email / password)")
    features = answers.get("features", "")
    extras   = answers.get("extras", "")

    lines = [f'Build an app called "{name}".']
    if desc:     lines.append(f"What it does: {desc}")
    if features: lines.append(f"Core features: {features}")
    if extras:   lines.append(f"Extra integrations: {extras}")
    if database: lines.append(f"Database: {database}")
    if auth:     lines.append(f"Authentication: {auth}")
    requirements = "\n".join(lines)

    return f"""## PROJECT: {name}
{requirements}

## TECH STACK
Frontend: {fe}
Backend:  {be}

## STEP 1 — ANALYZE AND DESIGN BEFORE ANY CODE
- What problem does this app solve? Who are the users?
- What data entities exist? List every model and its fields.
- What user roles exist? What can each role do?
- What are ALL the pages / routes / screens?
- What API endpoints does the backend need?
Draw the full architecture in your mind before writing file 1.

## STEP 1.5 — WRITE API_CONTRACT.md FIRST (the very first file, before ANY code)
Write <<<FILE:API_CONTRACT.md>>> in the project root containing:
  - Every endpoint: METHOD /api/path → request JSON (exact keys + types) →
    response JSON (exact keys + types) → status codes (200/201/400/401/404)
  - Ports: backend {_BE_PORTS.get(be, 5000)}, frontend {_FE_PORTS.get(fe, 5173)}
  - Env var names BOTH sides use (VITE_API_URL, DATABASE_URL, JWT_SECRET, ...)
  - Auth: header format (Authorization: Bearer <token>), token lifetimes

This contract is LAW for the rest of the build:
  - Backend routes implement it EXACTLY — same paths, same JSON keys
  - Frontend services/api.js calls it EXACTLY — never invent an endpoint not in it
  - If the design must change mid-build: edit API_CONTRACT.md FIRST, then update
    BOTH sides to match. The contract and the code must never disagree.
Frontend↔backend disconnection is the #1 way full-stack builds fail — the
contract is what prevents it. Never skip this step.

## STEP 2 — FOLDER STRUCTURE (non-negotiable)
All code in two top-level folders:
  frontend/   ← {fe}
  backend/    ← {be}

{_fe_detail(fe)}

{_be_detail(be)}

{_integration(fe, be)}

## STEP 3 — DATA MODELS & AUTH
Database: {database}
Auth: {auth}

Every model must have:
- Proper field types, constraints, indexes
- Created_at / updated_at timestamps
- Relationships defined correctly (FK, many-to-many)

Auth implementation:
- Passwords: bcrypt (cost 12) — NEVER MD5, SHA1, or plain text
- JWT: access token (15 min) + refresh token (7 days)
- Refresh tokens stored as SHA-256 hash in DB — never the raw token
- Protected routes: require valid access token in Authorization: Bearer header

## STEP 4 — DESIGN SYSTEM (use this exact palette — do NOT use grey defaults)

Dark theme CSS custom properties (put in index.css or variables.css):
  :root {{
    --bg:           #0f172a;   /* deep navy page background */
    --surface:      #1e293b;   /* card / panel background   */
    --surface-2:    #263348;   /* elevated surface, hover   */
    --border:       #334155;   /* subtle dividers           */
    --primary:      #6366f1;   /* indigo — buttons, links   */
    --primary-hover:#4f46e5;   /* darker on hover           */
    --accent:       #06b6d4;   /* cyan — highlights, badges */
    --success:      #10b981;   /* green — success states    */
    --error:        #ef4444;   /* red — errors, destructive */
    --warning:      #f59e0b;   /* amber — warnings          */
    --text:         #f1f5f9;   /* primary text              */
    --text-muted:   #94a3b8;   /* secondary text, labels    */
    --radius:       10px;      /* border radius             */
    --shadow:       0 4px 24px rgba(0,0,0,0.3);
  }}

Component patterns (apply consistently):
  Buttons:   background: var(--primary); border-radius: var(--radius); padding: 10px 20px; font-weight: 600;
  Cards:     background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius); padding: 24px;
  Inputs:    background: var(--surface); border: 1px solid var(--border); border-radius: 8px; padding: 10px 14px; color: var(--text);
  Navbar:    background: var(--surface); border-bottom: 1px solid var(--border); height: 60px;
  Sidebar:   background: var(--surface); border-right: 1px solid var(--border); width: 240px;
  Tables:    border-collapse: collapse; th background: var(--surface-2); td padding: 12px 16px; border-bottom: 1px solid var(--border);

Mobile-first: every layout must work at 375px width. Use CSS Grid and Flexbox.
Responsive breakpoints: 640px (sm), 768px (md), 1024px (lg).
Typography: system-ui font stack. Headings 700 weight. Body 400. Labels 500.

## STEP 5 — WRITE EVERY FILE using <<<FILE:absolute/path>>> marker

BUILD IN THIS ORDER (strictly — do not skip ahead):

Phase 0 — Contract:
  0. API_CONTRACT.md (from STEP 1.5 — the very first file)

Phase 1 — Backend:
  1. backend/.env.example
  2. backend/ config + models + database setup
  3. backend/ auth routes (register, login, /api/health)
  4. backend/ all other routes and services
  5. backend/ requirements.txt / package.json / go.mod

Phase 2 — Frontend:
  6. frontend/.env.example and vite.config.js (with proxy to backend)
  7. frontend/ services/api.js (axios with interceptors, uses VITE_API_URL)
  8. frontend/ auth pages (Login, Register)
  9. frontend/ all other pages and components
  10. frontend/ package.json

Phase 3 — Documentation:
  11. README.md with exact setup + run commands for BOTH sides

Writing rules:
  - Use <<<FILE:absolute/path>>> for EVERY file — no exceptions
  - Every file is complete — zero TODOs, zero stubs, zero "add your logic here"
  - Never write the same file twice — write it right the first time

## STEP 6 — RUNTIME VERIFICATION (actually RUN it — never just claim it works)
Run these for real with bash. Fix every error you hit before moving on:

  1. bash("cd " + backend folder + " && pip install -r requirements.txt", timeout=300)
     — a package-name error here means requirements.txt is wrong: fix it now
  2. bash("cd " + backend folder + " && python app.py", run_in_background=true)
     → returns process_id (e.g. 'proc-1')
  3. process_output(process_id="proc-1", wait_seconds=4)
     — any traceback in the boot log = fix the bug, restart, recheck
  4. web_fetch("http://localhost:{_BE_PORTS.get(be, 5000)}/api/health")
     — must return the ok status. If connection refused: wrong port or app crashed.
  5. bash("cd " + frontend folder + " && npm install", timeout=600)
  6. bash("cd " + frontend folder + " && npm run build", timeout=300)
     — the production build catches missing imports, bad paths, and broken JSX
     without needing a browser. Fix EVERY build error.
  7. process_kill(process_id="proc-1") — always stop the backend when done.

Only after all 7 pass, confirm the static checklist below:

  [ ] API_CONTRACT.md exists and every frontend API call matches a backend route
      in it — same path, same method, same JSON keys (grep the frontend for
      api. / fetch( / axios and cross-check each one)
  [ ] GET /api/health returns 200 {{"status": "ok"}}
  [ ] Backend CORS configured for http://localhost:{_FE_PORTS.get(fe, 5173)}
  [ ] vite.config.js proxy: /api → http://localhost:{_BE_PORTS.get(be, 5000)}
  [ ] frontend/.env: VITE_API_URL=http://localhost:{_BE_PORTS.get(be, 5000)}
  [ ] services/api.js uses import.meta.env.VITE_API_URL — no hardcoded URLs in code
  [ ] All imports in every file match the actual file paths created
  [ ] requirements.txt / package.json has every package the code imports
  [ ] No file contains TODO, placeholder, stub, or "implement this"
  [ ] README has working copy-paste commands for backend AND frontend setup

If any item is false, fix it before finishing."""


def _build_docker(answers: dict) -> str:
    database   = answers.get("database", "none")
    extras     = answers.get("extras", "none")
    multistage = answers.get("multistage", "yes")

    services = []
    if database != "none":    services.append(database)
    if "Redis" in (extras or ""):  services.append("Redis")
    if "Nginx" in (extras or ""):  services.append("Nginx")
    if "Celery" in (extras or ""): services.append("Celery worker")

    return f"""## DOCKER REQUIREMENTS
Database: {database}
Extra services: {extras}
Multi-stage build: {multistage}
Services in compose: app{', ' + ', '.join(services) if services else ''}

## WHAT TO BUILD

1. Read the project fully — understand language, framework, start command, and port.

2. Dockerfile:
{"   Multi-stage: builder stage (install + compile) → runtime stage (copy artifacts only)." if multistage == "yes" else "   Single-stage Dockerfile."}
   - Slim base image: python:3.11-slim, node:18-alpine, golang:1.21-alpine, etc.
   - Non-root user: RUN useradd -r appuser; USER appuser
   - Layer caching: COPY dependency files first, install, then COPY source code
   - EXPOSE correct port
   - HEALTHCHECK: CMD curl -f http://localhost:PORT/health || exit 1
   - CMD: exec form only: ["gunicorn", ...] not shell form

3. .dockerignore: __pycache__, node_modules, .env, .git, *.pyc, dist, build, coverage, *.log

4. docker-compose.yml:
   version: '3.9'
   - app: build: ., env_file: .env, depends_on with condition: service_healthy, restart: unless-stopped
{f"   - {database.lower()}: official image, named volume, healthcheck" if database != "none" else ""}
{"   - redis: redis:7-alpine, named volume" if "Redis" in (extras or "") else ""}
{"   - celery: same image as app, command: celery -A app.celery worker -l info" if "Celery" in (extras or "") else ""}
{"   - nginx: nginx:alpine, volumes for nginx.conf + static, ports: 80:80 443:443" if "Nginx" in (extras or "") else ""}

5. .env.example: every variable with example values
6. Makefile: build, up, down, logs, shell, test
7. README: docker-compose up --build and verify app starts

Write every file completely."""


def _build_ci(answers: dict) -> str:
    test_fw     = answers.get("test_fw", "")
    deploy      = answers.get("deploy", "none")
    auto_deploy = answers.get("auto_deploy", "no")

    def deploy_step(d):
        m = {"EC2": "SSH pull + restart", "ECS": "ECR push → ECS deploy",
             "Cloud Run": "GCR push → gcloud run deploy",
             "Azure": "az webapp container set", "VPS": "SSH docker-compose up -d",
             "Heroku": "heroku container:push + release"}
        for k, v in m.items():
            if k in d: return v
        return "deploy to target"

    def ci_secrets(d):
        m = {"EC2": "EC2_HOST, EC2_USER, EC2_SSH_KEY",
             "ECS": "AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, ECR_REPOSITORY, ECS_CLUSTER",
             "Cloud Run": "GCP_PROJECT_ID, GCP_SA_KEY",
             "Heroku": "HEROKU_API_KEY, HEROKU_APP_NAME",
             "VPS": "VPS_HOST, VPS_USER, VPS_SSH_KEY"}
        for k, v in m.items():
            if k in d: return f"  - {v}"
        return "  - No deployment secrets needed"

    return f"""## CI/CD REQUIREMENTS
Test framework: {test_fw or "detect from project"}
Deployment: {deploy}
Auto-deploy on main: {auto_deploy}

## WHAT TO BUILD

1. Read project to detect: language, test command, package manager, Dockerfile presence.

2. .github/workflows/ci.yml:
   Triggers: push/PR to main and develop
   Jobs (sequential, each depends_on previous):

   a. lint — checkout → setup → cache deps → install → lint
      Python: ruff / flake8. Node: eslint. Go: golangci-lint.

   b. test — checkout → setup → cache → install → run tests with coverage
      {test_fw or "Detect and run test command"}
      Upload coverage artifact.

   c. build — push to main only
      Build Docker image, tag :sha-XXXX and :latest, push to registry.

{"   d. deploy — push to main only" if auto_deploy == "yes" and "none" not in deploy else ""}
{"      " + deploy_step(deploy) if auto_deploy == "yes" and "none" not in deploy else ""}

3. Caching: hashFiles on requirements.txt / package-lock.json / go.sum
4. Secrets needed:
{ci_secrets(deploy)}
5. Add workflow_dispatch trigger for manual runs.
6. README: branch protection — require CI to pass before merge.

Write every file completely."""


_PROMPT_BUILDERS = {
    "newsite":   _build_newsite,
    "newapp":    _build_newapp,
    "docker":    _build_docker,
    "ci-github": _build_ci,
}


def build_skill_prompt(name: str, answers: dict) -> str | None:
    builder = _PROMPT_BUILDERS.get(name)
    if builder:
        return builder(answers)
    return get_skill_raw(name)


# ── Custom file skills (backward compat) ──────────────────────────────────────

BUILTIN_SKILLS = {k: f"[interactive — run via /skill {k}]" for k in _PROMPT_BUILDERS}


def get_skill_raw(name: str) -> str | None:
    SKILLS_DIR.mkdir(parents=True, exist_ok=True)
    f = SKILLS_DIR / f"{name}.md"
    return f.read_text(encoding="utf-8") if f.exists() else None


def get_skill(name: str) -> str | None:
    if name in _PROMPT_BUILDERS:
        return f"[Use ask_skill_questions('{name}') + build_skill_prompt() for this skill]"
    return get_skill_raw(name)


def load_skills() -> dict[str, str]:
    skills = dict(BUILTIN_SKILLS)
    SKILLS_DIR.mkdir(parents=True, exist_ok=True)
    for f in sorted(SKILLS_DIR.glob("*.md")):
        name = f.stem.lower().replace(" ", "-")
        if name not in skills:
            skills[name] = f.read_text(encoding="utf-8")
    return skills


def show_skills(console) -> None:
    builtin = list(_PROMPT_BUILDERS.keys())
    custom  = []
    SKILLS_DIR.mkdir(parents=True, exist_ok=True)
    for f in sorted(SKILLS_DIR.glob("*.md")):
        name = f.stem.lower().replace(" ", "-")
        if name not in builtin:
            custom.append((name, f.read_text(encoding="utf-8")))

    descriptions = {
        "newsite":   "Full-stack site — choose frontend + backend tech, frontend/ + backend/ folders",
        "newapp":    "Full-stack app  — choose frontend + backend tech, detailed per-framework rules",
        "docker":    "Dockerize — multi-stage build, compose, healthcheck, .dockerignore",
        "ci-github": "GitHub Actions — lint → test → build → deploy, secrets, caching",
    }

    console.print("\n[bold]Built-in Skills[/bold]  [dim](interactive Q&A)[/dim]")
    for name in builtin:
        console.print(f"  [green]{name:<18}[/green] [dim]{descriptions.get(name, '')}[/dim]")

    if custom:
        console.print("\n[bold]Custom Skills[/bold]  [dim](~/.bharatcode/skills/)[/dim]")
        for name, content in custom:
            preview = content.split("\n")[0][:60]
            console.print(f"  [cyan]{name:<18}[/cyan] [dim]{preview}[/dim]")

    console.print()
    console.print("[dim]Usage: /skill <name>   Custom: ~/.bharatcode/skills/<name>.md[/dim]\n")
