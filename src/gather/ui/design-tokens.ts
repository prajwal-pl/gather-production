/* ============================================================
   gather — design token references
   Do not edit these values directly — edit DESIGN_BRIEF.md first
   ============================================================ */

/* Light theme tokens (auto-injected by @layer theme) */
--background: oklch(1 0 0);
--foreground: oklch(0.145 0 0);
--card: oklch(1 0 0);
--card-foreground: oklch(0.145 0 0);
--muted: oklch(0.97 0 0);
--muted-foreground: oklch(0.556 0 0);
--primary: oklch(0.205 0 0);
--primary-foreground: oklch(0.985 0 0);
--success: oklch(0.472 0.205 143.33);
--destructive: oklch(0.577 0.245 27.325);
--border: oklch(0.922 0 0 / 0.3);
--ring: oklch(0.708 0 0);
--radius: 0rem;

/* Dark theme tokens (injected by .dark selector) */
.dark {
  --background: oklch(0.145 0 0);
  --foreground: oklch(0.985 0 0);
  --card: oklch(0.205 0 0);
  --card-foreground: oklch(0.985 0 0);
  --muted: oklch(0.269 0 0);
  --muted-foreground: oklch(0.708 0 0);
  --primary: oklch(0.922 0 0);
  --primary-foreground: oklch(0.205 0 0);
  --success: oklch(0.397 0.348 157.24);
  --destructive: oklch(0.704 0.191 22.216);
  --border: oklch(1 0 0 / 0.1);
  --ring: oklch(0.556 0 0);
  --radius: 0rem;
}

/* ============================================================
   Component usage guidelines (DO NOT override these in component code)
   ============================================================ */

# Button primaries:
--bg: var(--primary);
--color: var(--primary-foreground);

# Button secondaries:
--bg: var(--card);
--color: var(--muted-foreground);

# Input fills:
--bg: var(--background);
--border: var(--border);

# Badge backgrounds:
--success: var(--success);
--destructive: var(--destructive);

# List row active:
--bg: var(--primary)/10;

# Table even-row zebra:
--bg: var(--muted)/20;