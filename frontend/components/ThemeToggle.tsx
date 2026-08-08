"use client";

/**
 * Three states, not two: light, dark, and follow the system.
 *
 * A two-state toggle silently overrides the OS preference the first time it is
 * touched, with no way back. "System" has to be reachable, which is why this
 * cycles rather than flips.
 *
 * The `mounted` gate is not optional. `useTheme()` returns `undefined` on the
 * server and on the first client render — the resolved theme is only known
 * after next-themes' pre-paint script has run — so rendering the icon before
 * mount produces a hydration mismatch and a flash of the wrong glyph.
 */

import { Monitor, Moon, Sun } from "lucide-react";
import { useTheme } from "next-themes";
import { useSyncExternalStore } from "react";

const ORDER = ["system", "light", "dark"] as const;
const LABEL = {
  system: "Theme: follows your system",
  light: "Theme: light",
  dark: "Theme: dark",
} as const;

/** Never changes, so the store never notifies — the snapshots do all the work. */
const noSubscribe = () => () => {};

export function ThemeToggle() {
  const { theme, setTheme } = useTheme();
  // `false` during the server render and the hydration pass, `true` afterwards.
  // The useState + useEffect version of this is the same idea with a cascading
  // render, which React 19's compiler lint correctly objects to.
  const mounted = useSyncExternalStore(
    noSubscribe,
    () => true,
    () => false,
  );

  const current = (theme ?? "system") as (typeof ORDER)[number];
  const Icon = current === "light" ? Sun : current === "dark" ? Moon : Monitor;

  return (
    <button
      type="button"
      onClick={() => setTheme(ORDER[(ORDER.indexOf(current) + 1) % ORDER.length])}
      // Reserve the space before mount so the header does not reflow when the
      // icon appears.
      className="rounded-control p-1.5 text-ink-2 transition-colors duration-(--duration-hover) hover:bg-muted hover:text-ink"
      aria-label={mounted ? `${LABEL[current]}. Change theme.` : "Change theme"}
      title={mounted ? LABEL[current] : undefined}
    >
      {mounted ? (
        <Icon size={15} strokeWidth={2} aria-hidden />
      ) : (
        <span className="block size-[15px]" aria-hidden />
      )}
    </button>
  );
}
