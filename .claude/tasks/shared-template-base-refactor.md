# Shared Template Base Refactor

## Goal

Replace the duplicated per-service `base.html` templates with one shared base layout that all gateway and module pages extend, so header and footer changes live in one place.

## Reasoning

- The current layout drift happened because each service owns its own `templates/base.html`.
- All page templates already extend `base.html`, so the narrowest clean refactor is to keep that contract and change where `base.html` is resolved from.
- The gateway auth middleware already injects `x-user-id` and `x-user-role` headers for authenticated requests, which can be used by a shared base template without requiring different context objects in different apps.

## Plan

1. Create a small shared workspace package that exposes a `create_templates()` helper and a shared `templates/base.html`.
2. Update gateway and each mounted service to build `Jinja2Templates` through that helper so local templates resolve first and shared templates resolve second.
3. Remove the duplicated service-specific `base.html` files and standardize the shared base to use the auth headers for nav visibility.
4. Update `README.md` and `AGENTS.md` to document the new shared template architecture and how to change shared header/footer content.
5. Run the repository validation command and an additional focused smoke check for template loading if the repo still has no tests.

## Expected Outcome

- One shared definition of the site header and footer.
- Service pages keep their own content templates and styling hooks.
- Future branding or nav changes only need one template edit.

## Changes Made

### 1. Added a shared workspace package

- Created `services/media_tools_ui` with a `media_tools_ui` package.
- Added `media_tools_ui.create_templates()` to centralize Jinja setup.
- Added `media_tools_ui/templates/base.html` as the only shared site layout.

### 2. Standardized layout auth state

- The shared template helper now injects a `layout` context object from the auth headers already added by the gateway middleware.
- The shared base checks `layout.is_authenticated` and `layout.is_admin`, so the same layout works for gateway pages and mounted service pages.

### 3. Removed duplicated base templates

- Deleted the old `base.html` files from `gateway`, `spotify_dl`, `yt-bulk-dl`, and `edl-to-archive`.
- Existing page templates still extend `base.html`, but that file now resolves from the shared UI package.

### 4. Updated app wiring

- Added `media_tools_ui` as a workspace dependency for gateway and each mounted service.
- Switched all app-level Jinja initialization to `create_templates(local_templates_dir)`.

### 5. Updated documentation

- Documented the shared layout location and lookup order in `README.md`.
- Updated `AGENTS.md` so future changes follow the shared layout architecture.

### 6. Moved shared static assets

- Moved the global `style.css` and shared SVG logos into `services/media_tools_ui/static/`.
- Switched the gateway `/static` mount to `media_tools_ui.get_static_dir()` so all shared assets now come from the same package as the shared base template.